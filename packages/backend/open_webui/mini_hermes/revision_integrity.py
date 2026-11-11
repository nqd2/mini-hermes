"""Low-level checksum, object-store and revision lookup helpers."""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Preview, Revision
from .storage import ObjectStore

log = logging.getLogger(__name__)


def compute_patch_hash(*, base_revision: int, base_sha256: str, operations: list[dict], artifact_sha256: str) -> str:
    """Bind approval to the base revision, normalized operations and exact staged bytes."""
    canonical = json.dumps(
        {
            'base_revision': base_revision,
            'base_sha256': base_sha256,
            'operations': operations,
            'artifact_sha256': artifact_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


async def revision_for(db: AsyncSession, document_id: str, number: int) -> Revision:
    revision = (
        await db.execute(
            select(Revision).where(Revision.document_id == document_id, Revision.number == number)
        )
    ).scalar_one_or_none()
    if revision is None:
        raise HTTPException(404, 'Revision not found')
    return revision


async def get_verified_revision_content(store: ObjectStore, revision: Revision) -> bytes:
    """Fail closed and alert if immutable bytes no longer match committed metadata."""
    try:
        content = await store.get(revision.object_key)
    except Exception as exc:
        log.critical(
            'Committed document revision object is unavailable: revision_id=%s document_id=%s object_key=%s',
            revision.id,
            revision.document_id,
            revision.object_key,
            exc_info=exc,
        )
        raise HTTPException(
            status_code=503,
            detail={
                'code': 'document_revision_integrity_failure',
                'message': 'This committed document revision failed its integrity check and is temporarily unavailable.',
                'revision': revision.number,
            },
        ) from exc
    digest = hashlib.sha256(content).hexdigest()
    if digest != revision.sha256 or len(content) != revision.size:
        log.critical(
            'Committed document revision integrity failure: revision_id=%s document_id=%s expected_sha256=%s actual_sha256=%s expected_size=%s actual_size=%s',
            revision.id,
            revision.document_id,
            revision.sha256,
            digest,
            revision.size,
            len(content),
        )
        raise HTTPException(
            status_code=503,
            detail={
                'code': 'document_revision_integrity_failure',
                'message': 'This committed document revision failed its integrity check and is temporarily unavailable.',
                'revision': revision.number,
            },
        )
    return content


async def ensure_preview_hashes(
    db: AsyncSession,
    store: ObjectStore,
    preview: Preview,
    *,
    commit: bool = True,
) -> None:
    """Backfill hashes for previews staged before hash-bound approvals were deployed."""
    if not preview.preview_key:
        return
    if not preview.base_sha256:
        base = await revision_for(db, preview.document_id, preview.base_revision)
        base_content = await get_verified_revision_content(store, base)
        preview.base_sha256 = hashlib.sha256(base_content).hexdigest()
    if not preview.artifact_sha256:
        preview.artifact_sha256 = hashlib.sha256(await store.get(preview.preview_key)).hexdigest()
    if not preview.patch_hash and preview.base_sha256 and preview.artifact_sha256:
        preview.patch_hash = compute_patch_hash(
            base_revision=preview.base_revision,
            base_sha256=preview.base_sha256,
            operations=preview.operations,
            artifact_sha256=preview.artifact_sha256,
        )
    if commit:
        await db.commit()


async def put_immutable(store: ObjectStore, key: str, content: bytes, mime_type: str) -> None:
    """Use conditional object-store writes when supported, then verify at callers."""
    put_if_absent = getattr(store, 'put_immutable', None)
    if put_if_absent is not None:
        await put_if_absent(key, content, mime_type)
    else:
        # Legacy adapters and test stores can still be used because publication
        # reads back and verifies the exact object before its database commit.
        await store.put(key, content, mime_type)
