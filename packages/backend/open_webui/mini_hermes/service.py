"""Revision transactions and preview processing."""

from __future__ import annotations

import difflib
import hashlib
import io
import zipfile
from pathlib import PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .drive import NATIVE_TO_OFFICE
from .edits import apply_operations, extract_text, normalize_operations
from .library import SUPPORTED_RESOURCE_SUFFIXES, ensure_personal_folder
from .models import Document, Preview, Revision, SyncJob, now
from .storage import ObjectStore

SUPPORTED = {'.docx', '.xlsx', '.pptx', '.pdf'}
MIME_TYPES = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.pdf': 'application/pdf',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.csv': 'text/csv',
    '.tsv': 'text/tab-separated-values',
    '.json': 'application/json',
}
MAX_BYTES = 20 * 1024 * 1024


def validate_generated_content(content: bytes, suffix: str) -> None:
    """Reject corrupt Office output before it enters the revision store."""
    if suffix in {'.docx', '.xlsx', '.pptx'}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                    raise ValueError('Office archive is too large')
            if suffix == '.docx':
                from docx import Document as WordDocument

                WordDocument(io.BytesIO(content))
            elif suffix == '.xlsx':
                from openpyxl import load_workbook

                workbook = load_workbook(io.BytesIO(content), read_only=True)
                workbook.close()
            else:
                from pptx import Presentation

                Presentation(io.BytesIO(content))
        except Exception as exc:
            raise HTTPException(422, f'Invalid {suffix.upper()[1:]} output') from exc
    elif suffix == '.pdf' and not content.startswith(b'%PDF-'):
        raise HTTPException(422, 'Invalid PDF output')


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


async def ensure_preview_hashes(db: AsyncSession, store: ObjectStore, preview: Preview, *, commit: bool = True) -> None:
    """Backfill hashes for previews staged before hash-bound approvals were deployed."""
    if not preview.preview_key:
        return
    if not preview.base_sha256:
        base = await revision_for(db, preview.document_id, preview.base_revision)
        preview.base_sha256 = hashlib.sha256(await store.get(base.object_key)).hexdigest()
    if not preview.artifact_sha256:
        preview.artifact_sha256 = hashlib.sha256(await store.get(preview.preview_key)).hexdigest()
    if commit:
        await db.commit()


async def create_document(db: AsyncSession, store: ObjectStore, user_id: str, filename: str,
                          content: bytes, drive_file_id: str | None = None,
                          drive_mime_type: str | None = None, drive_version: str | None = None,
                          folder_id: str | None = None, source_file_id: str | None = None) -> Document:
    from pathlib import Path

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_RESOURCE_SUFFIXES:
        raise HTTPException(400, 'Supported resource formats: DOCX, XLSX, PPTX, PDF, TXT, MD, CSV, TSV and JSON')
    if len(content) > MAX_BYTES:
        raise HTTPException(413, 'File exceeds 20 MB limit')
    if suffix in SUPPORTED:
        validate_generated_content(content, suffix)
    if source_file_id:
        existing = (await db.execute(select(Document).where(Document.source_file_id == source_file_id))).scalar_one_or_none()
        if existing:
            if existing.owner_id != user_id:
                raise HTTPException(404, 'Document not found')
            return existing
    if folder_id is None:
        folder_id = (await ensure_personal_folder(db, user_id)).id
    else:
        from .library import accessible_folder

        folder = await accessible_folder(db, folder_id, user_id, 'write')
        folder_id = folder.id
    document_id = str(uuid4())
    revision_id = str(uuid4())
    key = f'users/{user_id}/documents/{document_id}/revisions/{revision_id}{suffix}'
    mime_type = MIME_TYPES[suffix]
    await store.put(key, content, mime_type)
    doc = Document(id=document_id, owner_id=user_id, filename=filename, mime_type=mime_type,
                   folder_id=folder_id, source_file_id=source_file_id, description_status='pending',
                   current_revision=1, drive_file_id=drive_file_id, drive_mime_type=drive_mime_type,
                   drive_version=drive_version)
    db.add(doc)
    db.add(Revision(id=revision_id, document_id=document_id, number=1, object_key=key,
                    sha256=hashlib.sha256(content).hexdigest(), size=len(content), operations=None))
    from .resource_indexing import enqueue_resource_index_job, resource_index_worker_enabled

    if resource_index_worker_enabled():
        await enqueue_resource_index_job(db, document_id, 1)
    else:
        from .chunks import replace_document_chunks

        await replace_document_chunks(db, document_id, 1, filename, content)
    await db.commit()
    return doc


async def publish_generated_document(
    db: AsyncSession, store: ObjectStore, user_id: str, output_path: str,
    content: bytes, output_key: str, source_document_id: str | None = None,
    is_admin: bool = False, expected_revision: int | None = None,
) -> tuple[Document, Revision, SyncJob | None]:
    """Save new sandbox output; edits to existing resources require a preview."""
    filename = PurePosixPath(output_path).name
    suffix = PurePosixPath(filename).suffix.lower()
    if not filename or suffix not in SUPPORTED:
        raise HTTPException(400, 'Supported output formats: DOCX, XLSX, PPTX and PDF')
    if len(content) > MAX_BYTES:
        raise HTTPException(413, 'File exceeds 20 MB limit')
    if source_document_id is not None:
        if suffix == '.pdf':
            raise HTTPException(400, 'PDF is read-only')
        raise HTTPException(409, {
            'code': 'explicit_preview_required',
            'message': 'Updates to an existing document must use a staged preview and explicit approval.',
        })
    digest = hashlib.sha256(content).hexdigest()
    publication_key = hashlib.sha256(f'{user_id}:{source_document_id or "new"}:{output_key}:{digest}'.encode()).hexdigest()
    source_file_id = f'sandbox:{publication_key}'
    doc = await create_document(db, store, user_id, filename, content, source_file_id=source_file_id)
    revision = await revision_for(db, doc.id, 1)
    return doc, revision, None


async def create_preview(db: AsyncSession, doc: Document, user_id: str, operations: list[dict]) -> Preview:
    try:
        operations = normalize_operations(operations)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    base = await revision_for(db, doc.id, doc.current_revision)
    preview = Preview(id=str(uuid4()), document_id=doc.id, owner_id=user_id,
                      base_revision=doc.current_revision, base_sha256=base.sha256,
                      drive_version=doc.drive_version,
                      operations=operations, status='pending')
    db.add(preview)
    await db.commit()
    return preview


async def create_artifact_preview(
    db: AsyncSession, store: ObjectStore, doc: Document, user_id: str, content: bytes,
) -> Preview:
    """Stage a complete sandbox output as a version-bound proposal for an existing document."""
    suffix = PurePosixPath(doc.filename).suffix.lower()
    if suffix == '.pdf':
        raise HTTPException(400, 'PDF is read-only')
    if suffix not in SUPPORTED:
        raise HTTPException(400, 'Unsupported document format')
    if len(content) > MAX_BYTES:
        raise HTTPException(413, 'File exceeds 20 MB limit')
    try:
        validate_generated_content(content, suffix)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    base = await revision_for(db, doc.id, doc.current_revision)
    preview = Preview(
        id=str(uuid4()), document_id=doc.id, owner_id=user_id,
        base_revision=doc.current_revision, base_sha256=base.sha256,
        drive_version=doc.drive_version, operations=[{'op': 'sandbox_publish'}], status='pending',
    )
    key = f'documents/{doc.id}/previews/{preview.id}'
    preview.preview_key = key
    db.add(preview)
    # Persist the stage key before writing to SeaweedFS so abandoned uploads are collectible.
    await db.commit()
    try:
        await store.put(key, content, doc.mime_type)
        stored = await store.get(key)
        artifact_sha256 = hashlib.sha256(content).hexdigest()
        if hashlib.sha256(stored).hexdigest() != artifact_sha256:
            raise IOError('Staged artifact checksum verification failed')
        before = await store.get(base.object_key)
        if hashlib.sha256(before).hexdigest() != preview.base_sha256:
            raise IOError('Base revision checksum verification failed')
        old_text = [line + '\n' for line in extract_text(before, doc.filename).splitlines()]
        new_text = [line + '\n' for line in extract_text(content, doc.filename).splitlines()]
        diff = ''.join(difflib.unified_diff(old_text, new_text, fromfile='before', tofile='after'))
        preview.artifact_sha256 = artifact_sha256
        preview.diff = (diff or 'No text changes; review the preview file for formatting changes.\n')[:100_000]
        preview.status = 'ready'
    except Exception as exc:
        preview.status = 'failed'
        preview.error = str(exc)[:2000]
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
        preview.preview_key = key
        await db.commit()
        await store.put(key, after, doc.mime_type)
        stored = await store.get(key)
        artifact_sha256 = hashlib.sha256(after).hexdigest()
        if hashlib.sha256(stored).hexdigest() != artifact_sha256:
            raise IOError('Staged artifact checksum verification failed')
        if hashlib.sha256(before).hexdigest() != preview.base_sha256:
            raise IOError('Base revision checksum verification failed')
        preview.artifact_sha256 = artifact_sha256
        preview.diff = (diff or 'No text changes; review the preview file for formatting changes.\n')[:100_000]
        preview.status = 'ready'
    except Exception as exc:
        preview.status = 'failed'
        preview.error = str(exc)[:2000]
    await db.commit()


async def commit_preview(
    db: AsyncSession, store: ObjectStore, user_id: str, preview_id: str, is_admin: bool = False,
    sync: bool = True, approval: dict | None = None,
) -> tuple[Revision, SyncJob | None]:
    # An existing revision is the idempotent response, even after a client retry.
    existing = (await db.execute(select(Revision).where(Revision.preview_id == preview_id))).scalar_one_or_none()
    if existing:
        job = (await db.execute(select(SyncJob).where(SyncJob.revision_id == existing.id))).scalar_one_or_none()
        from .library import accessible_document

        await accessible_document(db, existing.document_id, user_id, 'write', is_admin=is_admin)
        return existing, job
    preview = (await db.execute(select(Preview).where(Preview.id == preview_id, Preview.owner_id == user_id))).scalar_one_or_none()
    if not preview:
        raise HTTPException(404, 'Preview not found')
    from .library import accessible_document

    doc = await accessible_document(db, preview.document_id, user_id, 'write', is_admin=is_admin, lock=True)
    if preview.status != 'ready' or not preview.preview_key:
        raise HTTPException(409, 'Preview is not ready')
    await ensure_preview_hashes(db, store, preview, commit=False)
    expected_approval = {
        'base_revision': preview.base_revision,
        'base_sha256': preview.base_sha256,
        'artifact_sha256': preview.artifact_sha256,
    }
    if not approval or any(approval.get(key) != value for key, value in expected_approval.items()):
        raise HTTPException(400, {
            'code': 'explicit_preview_approval_required',
            'message': 'Confirm the exact preview after reviewing its diff and artifact.',
            **expected_approval,
        })
    if doc.current_revision != preview.base_revision:
        raise HTTPException(409, 'Document has changed since preview')
    # Recheck while the document row is locked, covering concurrent commits.
    existing = (await db.execute(select(Revision).where(Revision.preview_id == preview_id))).scalar_one_or_none()
    if existing:
        job = (await db.execute(select(SyncJob).where(SyncJob.revision_id == existing.id))).scalar_one_or_none()
        await accessible_document(db, existing.document_id, user_id, 'write', is_admin=is_admin)
        return existing, job
    content = await store.get(preview.preview_key)
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    if artifact_sha256 != preview.artifact_sha256:
        raise HTTPException(409, 'Preview artifact checksum changed; create a new preview')
    base_revision = await revision_for(db, doc.id, preview.base_revision)
    if base_revision.sha256 != preview.base_sha256:
        raise HTTPException(409, 'Base revision checksum changed; create a new preview')
    preview.approved_at = now()
    doc.current_revision += 1
    preview.status = 'committed'
    revision = Revision(id=str(uuid4()), document_id=doc.id, number=doc.current_revision,
                        object_key=preview.preview_key, sha256=hashlib.sha256(content).hexdigest(), size=len(content),
                        preview_id=preview.id, operations=preview.operations)
    job = None
    if sync and doc.drive_file_id:
        job = SyncJob(id=str(uuid4()), document_id=doc.id, revision_id=revision.id,
                      status='pending', attempts=0)
        db.add(job)
    db.add(revision)
    from .resource_indexing import enqueue_resource_index_job, resource_index_worker_enabled

    if resource_index_worker_enabled():
        await enqueue_resource_index_job(db, doc.id, doc.current_revision)
    else:
        from .chunks import replace_document_chunks

        await replace_document_chunks(db, doc.id, doc.current_revision, doc.filename, content)
    await db.commit()
    return revision, job
