"""Revision transactions and preview processing."""

from __future__ import annotations

import difflib
import hashlib
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .drive import NATIVE_TO_OFFICE
from .edits import apply_operations, extract_text
from .models import Document, Preview, Revision, SyncJob
from .storage import ObjectStore


SUPPORTED = {'.docx', '.xlsx', '.pptx', '.pdf'}
MIME_TYPES = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.pdf': 'application/pdf',
}
MAX_BYTES = 20 * 1024 * 1024


async def owned_document(db: AsyncSession, document_id: str, user_id: str, lock=False) -> Document:
    query = select(Document).where(Document.id == document_id, Document.owner_id == user_id)
    if lock:
        query = query.with_for_update()
    doc = (await db.execute(query)).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, 'Document not found')
    return doc


async def revision_for(db: AsyncSession, document_id: str, number: int) -> Revision:
    revision = (await db.execute(select(Revision).where(Revision.document_id == document_id, Revision.number == number))).scalar_one_or_none()
    if revision is None:
        raise HTTPException(404, 'Revision not found')
    return revision


async def create_document(db: AsyncSession, store: ObjectStore, user_id: str, filename: str,
                          content: bytes, drive_file_id: str | None = None,
                          drive_mime_type: str | None = None, drive_version: str | None = None) -> Document:
    from pathlib import Path

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(400, 'Supported formats: DOCX, XLSX, PPTX and PDF')
    if len(content) > MAX_BYTES:
        raise HTTPException(413, 'File exceeds 20 MB limit')
    document_id = str(uuid4())
    revision_id = str(uuid4())
    key = f'documents/{document_id}/revisions/{revision_id}{suffix}'
    mime_type = MIME_TYPES[suffix]
    await store.put(key, content, mime_type)
    doc = Document(id=document_id, owner_id=user_id, filename=filename, mime_type=mime_type,
                   current_revision=1, drive_file_id=drive_file_id, drive_mime_type=drive_mime_type,
                   drive_version=drive_version)
    db.add(doc)
    db.add(Revision(id=revision_id, document_id=document_id, number=1, object_key=key,
                    sha256=hashlib.sha256(content).hexdigest(), size=len(content), operations=None))
    await db.commit()
    return doc


async def create_preview(db: AsyncSession, doc: Document, user_id: str, operations: list[dict]) -> Preview:
    if doc.filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'PDF is read-only')
    if not operations or len(operations) > 100:
        raise HTTPException(400, 'Provide 1 to 100 operations')
    if doc.drive_mime_type in NATIVE_TO_OFFICE:
        allowed = {
            'application/vnd.google-apps.document': {'replace_text', 'append_paragraph'},
            'application/vnd.google-apps.spreadsheet': {'set_cell', 'add_sheet', 'append_row'},
            'application/vnd.google-apps.presentation': {'replace_text', 'add_slide'},
        }[doc.drive_mime_type]
        if any(op.get('op') not in allowed for op in operations):
            raise HTTPException(400, 'Operation cannot be synchronized to this Google-native file')
        if any((op['op'] == 'set_cell' and any(key in op for key in ('number_format', 'bold', 'fill')))
               or (op['op'] == 'append_paragraph' and 'style' in op)
               or (op['op'] == 'add_slide' and op.get('layout', 6) != 6)
               or (op['op'] == 'set_cell' and isinstance(op.get('value'), str) and op['value'].startswith('='))
               or (op['op'] == 'append_row' and any(isinstance(value, str) and value.startswith('=')
                                                   for value in op.get('values', [])))
               for op in operations):
            raise HTTPException(400, 'Formatting or formula option cannot be synchronized to this Google-native file')
    preview = Preview(id=str(uuid4()), document_id=doc.id, owner_id=user_id,
                      base_revision=doc.current_revision, drive_version=doc.drive_version,
                      operations=operations, status='pending')
    db.add(preview)
    await db.commit()
    return preview


async def build_preview(db: AsyncSession, store: ObjectStore, preview: Preview) -> None:
    doc = (await db.execute(select(Document).where(Document.id == preview.document_id))).scalar_one()
    base = await revision_for(db, doc.id, preview.base_revision)
    before = await store.get(base.object_key)
    try:
        after = apply_operations(before, doc.filename, preview.operations)
        old_text = [line + '\n' for line in extract_text(before, doc.filename).splitlines()]
        new_text = [line + '\n' for line in extract_text(after, doc.filename).splitlines()]
        diff = ''.join(difflib.unified_diff(old_text, new_text, fromfile='before', tofile='after'))
        key = f'documents/{doc.id}/previews/{preview.id}'
        await store.put(key, after, doc.mime_type)
        preview.preview_key = key
        preview.diff = (diff or 'No text changes; review the preview file for formatting changes.\n')[:100_000]
        preview.status = 'ready'
    except Exception as exc:
        preview.status = 'failed'
        preview.error = str(exc)[:2000]
    await db.commit()


async def commit_preview(db: AsyncSession, store: ObjectStore, user_id: str, preview_id: str) -> tuple[Revision, SyncJob]:
    # An existing revision is the idempotent response, even after a client retry.
    existing = (await db.execute(select(Revision).where(Revision.preview_id == preview_id))).scalar_one_or_none()
    if existing:
        job = (await db.execute(select(SyncJob).where(SyncJob.revision_id == existing.id))).scalar_one()
        await owned_document(db, existing.document_id, user_id)
        return existing, job
    preview = (await db.execute(select(Preview).where(Preview.id == preview_id, Preview.owner_id == user_id))).scalar_one_or_none()
    if not preview:
        raise HTTPException(404, 'Preview not found')
    if preview.status != 'ready' or not preview.preview_key:
        raise HTTPException(409, 'Preview is not ready')
    doc = await owned_document(db, preview.document_id, user_id, lock=True)
    if doc.current_revision != preview.base_revision:
        raise HTTPException(409, 'Document has changed since preview')
    # Recheck while the document row is locked, covering concurrent commits.
    existing = (await db.execute(select(Revision).where(Revision.preview_id == preview_id))).scalar_one_or_none()
    if existing:
        job = (await db.execute(select(SyncJob).where(SyncJob.revision_id == existing.id))).scalar_one()
        return existing, job
    content = await store.get(preview.preview_key)
    doc.current_revision += 1
    preview.status = 'committed'
    revision = Revision(id=str(uuid4()), document_id=doc.id, number=doc.current_revision,
                        object_key=preview.preview_key, sha256=hashlib.sha256(content).hexdigest(), size=len(content),
                        preview_id=preview.id, operations=preview.operations)
    job = SyncJob(id=str(uuid4()), document_id=doc.id, revision_id=revision.id,
                  status='pending', attempts=0)
    db.add_all([revision, job])
    await db.commit()
    return revision, job
