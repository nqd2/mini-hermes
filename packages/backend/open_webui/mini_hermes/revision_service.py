"""Authoritative approval and atomic publication operations for document revisions."""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Preview,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
    now,
)
from .revision_integrity import (
    compute_patch_hash,
    ensure_preview_hashes,
    get_verified_revision_content,
    revision_for,
)
from .revision_integrity import (
    put_immutable as _put_immutable,
)
from .storage import ObjectStore

log = logging.getLogger(__name__)

APPROVAL_TTL = dt.timedelta(minutes=15)


def _approval_expires_at(preview: Preview):
    """Keep approval expiry fixed to preview creation, including legacy rows."""
    created_at = preview.created_at
    if created_at is None:
        return None
    # SQLite drops timezone metadata for DateTime(timezone=True); these values
    # are written by now() in UTC, so restore UTC before comparing or persisting.
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=dt.UTC)
    return created_at + APPROVAL_TTL


async def prepare_rollback_preview(
    db: AsyncSession,
    store: ObjectStore,
    user_id: str,
    document_id: str,
    *,
    target_revision: int,
    expected_current_revision: int,
    is_admin: bool = False,
) -> Preview:
    """Stage an immutable compensating patch that restores one verified prior revision."""
    from .edits import extract_text
    from .library import accessible_document

    if target_revision < 1 or expected_current_revision < 1:
        raise HTTPException(status_code=422, detail='Revision numbers must be positive.')
    doc = await accessible_document(db, document_id, user_id, 'write', is_admin=is_admin, lock=True)
    if doc.current_revision != expected_current_revision:
        raise HTTPException(status_code=409, detail='Document changed before rollback preview was prepared.')
    if target_revision >= doc.current_revision:
        raise HTTPException(status_code=409, detail='Rollback target must be an earlier revision.')

    base = await revision_for(db, doc.id, doc.current_revision)
    target = await revision_for(db, doc.id, target_revision)
    base_content = await get_verified_revision_content(store, base)
    target_content = await get_verified_revision_content(store, target)
    artifact_sha256 = hashlib.sha256(target_content).hexdigest()
    operations = [{'op': 'rollback', 'target_revision': target_revision}]
    preview_id = str(uuid4())
    preview = Preview(
        id=preview_id,
        document_id=doc.id,
        owner_id=user_id,
        base_revision=doc.current_revision,
        base_sha256=base.sha256,
        artifact_sha256=artifact_sha256,
        patch_hash=compute_patch_hash(
            base_revision=doc.current_revision,
            base_sha256=base.sha256,
            operations=operations,
            artifact_sha256=artifact_sha256,
        ),
        drive_version=doc.drive_version,
        operations=operations,
        status='pending',
        preview_key=f'documents/{doc.id}/staged/previews/{preview_id}/{artifact_sha256}',
    )
    db.add(preview)
    # Persist the stage key first so a crash leaves a collectable preview record.
    await db.commit()
    try:
        await _put_immutable(store, preview.preview_key, target_content, doc.mime_type)
        staged = await store.get(preview.preview_key)
        if hashlib.sha256(staged).hexdigest() != artifact_sha256:
            raise OSError('Staged rollback checksum verification failed')
        try:
            before_text = extract_text(base_content, doc.filename)
            after_text = extract_text(staged, doc.filename)
            before_lines = [line + '\n' for line in before_text.splitlines()]
            after_lines = [line + '\n' for line in after_text.splitlines()]
            diff = ''.join(
                difflib.unified_diff(before_lines, after_lines, fromfile='current', tofile='rollback target')
            )
        except Exception:
            diff = f'Binary rollback to revision {target_revision}; review the proposed artifact before approval.\n'
        preview.diff = (diff or 'Rollback target has no text changes.\n')[:100_000]
        preview.status = 'ready'
    except Exception as exc:
        preview.status = 'failed'
        preview.error = str(exc)[:2000]
    await db.commit()
    return preview


async def commit_preview(
    db: AsyncSession,
    store: ObjectStore,
    user_id: str,
    preview_id: str,
    is_admin: bool = False,
    sync: bool = True,
    approval: dict | None = None,
    idempotency_key: str | None = None,
) -> tuple[Revision, SyncJob | None]:
    """Commit the exact approved staged object at one database visibility point."""
    from .library import accessible_document

    commit_key = _commit_key(idempotency_key, f'preview:{preview_id}')
    existing = (
        await db.execute(select(Revision).where(Revision.preview_id == preview_id))
    ).scalar_one_or_none()
    if existing:
        await accessible_document(db, existing.document_id, user_id, 'write', is_admin=is_admin)
        job = (
            await db.execute(select(SyncJob).where(SyncJob.revision_id == existing.id))
        ).scalar_one_or_none()
        return existing, job

    same_key = (
        await db.execute(select(Revision).where(Revision.idempotency_key == commit_key))
    ).scalar_one_or_none()
    if same_key:
        if same_key.preview_id != preview_id:
            raise HTTPException(
                status_code=409,
                detail={'code': 'idempotency_key_reused', 'message': 'Commit key belongs to another revision.'},
            )
        await accessible_document(db, same_key.document_id, user_id, 'write', is_admin=is_admin)
        job = (
            await db.execute(select(SyncJob).where(SyncJob.revision_id == same_key.id))
        ).scalar_one_or_none()
        return same_key, job

    preview = (
        await db.execute(select(Preview).where(Preview.id == preview_id).with_for_update())
    ).scalar_one_or_none()
    if preview is None:
        raise HTTPException(status_code=404, detail='Preview not found')
    doc = await accessible_document(db, preview.document_id, user_id, 'write', is_admin=is_admin, lock=True)
    if preview.status != 'ready' or not preview.preview_key:
        raise HTTPException(status_code=409, detail='Preview is not ready')
    approval_expires_at = _approval_expires_at(preview)
    if approval_expires_at is None or approval_expires_at <= now():
        raise HTTPException(status_code=409, detail='Preview approval expired')

    await ensure_preview_hashes(db, store, preview, commit=False)
    expected = {
        'base_revision': preview.base_revision,
        'base_sha256': preview.base_sha256,
        'artifact_sha256': preview.artifact_sha256,
        'patch_hash': preview.patch_hash,
    }
    if (
        not approval
        or any(approval.get(key) != value for key, value in expected.items())
    ):
        raise HTTPException(
            status_code=400,
            detail={
                'code': 'explicit_preview_approval_required',
                'message': 'Confirm the exact preview after reviewing its diff and artifact.',
                **expected,
            },
        )

    if doc.current_revision != preview.base_revision:
        raise HTTPException(status_code=409, detail='Document has changed since preview')

    base_revision = await revision_for(db, doc.id, preview.base_revision)
    base_content = await store.get(base_revision.object_key)
    if (
        hashlib.sha256(base_content).hexdigest() != preview.base_sha256
        or base_revision.sha256 != preview.base_sha256
    ):
        raise HTTPException(status_code=409, detail='Base revision checksum changed; create a new preview')

    content = await store.get(preview.preview_key)
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    if artifact_sha256 != preview.artifact_sha256:
        raise HTTPException(status_code=409, detail='Preview artifact checksum changed; create a new preview')
    patch_hash = compute_patch_hash(
        base_revision=preview.base_revision,
        base_sha256=preview.base_sha256,
        operations=preview.operations,
        artifact_sha256=artifact_sha256,
    )
    if preview.patch_hash != patch_hash or (
        approval['patch_hash'] != patch_hash
    ):
        raise HTTPException(status_code=409, detail='Preview patch changed; create a new preview')

    # Recheck permissions and the base revision immediately before publication.
    doc = await accessible_document(db, doc.id, user_id, 'write', is_admin=is_admin, lock=True)
    if doc.current_revision != preview.base_revision:
        raise HTTPException(status_code=409, detail='Document has changed since preview')
    consumed_at = now()
    if approval_expires_at <= consumed_at:
        raise HTTPException(status_code=409, detail='Preview approval expired')
    approval_record = RevisionApproval(
        id=str(uuid4()),
        idempotency_key=commit_key,
        preview_id=preview.id,
        user_id=preview.owner_id,
        resource_id=doc.id,
        base_revision=preview.base_revision,
        patch_hash=patch_hash,
        approver_id=user_id,
        expires_at=approval_expires_at,
        created_at=consumed_at,
        consumed_at=consumed_at,
    )
    if approval_record.expires_at <= consumed_at:
        raise HTTPException(status_code=409, detail='Preview approval expired')

    next_revision_number = doc.current_revision + 1
    revision = Revision(
        id=str(uuid4()),
        document_id=doc.id,
        number=next_revision_number,
        object_key=preview.preview_key,
        sha256=artifact_sha256,
        size=len(content),
        preview_id=preview.id,
        idempotency_key=commit_key,
        operations=preview.operations,
    )
    job = None
    if sync and doc.drive_file_id:
        job = SyncJob(
            id=str(uuid4()), document_id=doc.id, revision_id=revision.id, status='pending', attempts=0
        )
        db.add(job)
    db.add_all(
        [
            approval_record,
            revision,
            RevisionOutbox(
                id=str(uuid4()),
                event_key=f'revision.committed:{revision.id}',
                document_id=doc.id,
                revision_id=revision.id,
                event_type='revision.committed',
                payload={
                    'document_id': doc.id,
                    'revision': next_revision_number,
                    'revision_id': revision.id,
                    'actor_id': user_id,
                },
                attempts=0,
                created_at=consumed_at,
            ),
        ]
    )
    doc.current_revision = next_revision_number
    preview.approved_at = consumed_at
    preview.patch_hash = patch_hash
    preview.status = 'committed'

    from .resource_indexing import enqueue_resource_index_job, resource_index_worker_enabled

    if resource_index_worker_enabled():
        await enqueue_resource_index_job(db, doc.id, next_revision_number)
    else:
        from .chunks import replace_document_chunks

        await replace_document_chunks(db, doc.id, next_revision_number, doc.filename, content)

    # The database commit atomically publishes the revision pointer, approval,
    # revision metadata, index/sync jobs and durable outbox event. The staged
    # immutable object was already checksum-verified above.
    await db.commit()
    return revision, job


def _commit_key(request_key: str | None, fallback: str) -> str:
    key = request_key.strip() if isinstance(request_key, str) else ''
    key = key or fallback
    if len(key) > 255:
        return f'sha256:{hashlib.sha256(key.encode("utf-8")).hexdigest()}'
    return key


async def dispatch_pending_outbox(
    db: AsyncSession,
    request,
    revision_id: str | None = None,
    *,
    limit: int = 100,
) -> int:
    """Publish committed revision events at least once with a stable event ID."""
    from open_webui.events import EVENTS, build_event, publish_event

    current_time = now()
    async with db.begin():
        query = select(RevisionOutbox).where(
            RevisionOutbox.published_at.is_(None),
            (RevisionOutbox.lease_until.is_(None) | (RevisionOutbox.lease_until <= current_time)),
        )
        if revision_id:
            query = query.where(RevisionOutbox.revision_id == revision_id)
        rows = list(
            (await db.scalars(query.order_by(RevisionOutbox.created_at).limit(max(1, min(limit, 500)))
                              .with_for_update(skip_locked=True))).all()
        )
        for row in rows:
            row.attempts += 1
            row.lease_until = current_time + dt.timedelta(seconds=60)

    published = 0
    for row in rows:
        try:
            payload = dict(row.payload or {})
            actor_id = payload.pop('actor_id', None)
            event = build_event(
                request,
                EVENTS.DOCUMENT_REVISION_COMMITTED,
                actor={'id': actor_id, 'type': 'user'} if actor_id else None,
                subject_id=row.document_id,
                subject_type='document',
                source='mini_hermes',
                data={**payload, 'event_key': row.event_key},
                event_id=row.event_key,
            )
            await publish_event(request, event)
            async with db.begin():
                current = await db.scalar(
                    select(RevisionOutbox).where(RevisionOutbox.id == row.id).with_for_update()
                )
                if current and current.published_at is None:
                    current.published_at = now()
                    current.lease_until = None
                    published += 1
        except Exception:
            log.exception('Revision outbox delivery failed: event_key=%s', row.event_key)
            async with db.begin():
                current = await db.scalar(
                    select(RevisionOutbox).where(RevisionOutbox.id == row.id).with_for_update()
                )
                if current and current.published_at is None:
                    current.lease_until = now() + dt.timedelta(
                        seconds=min(3600, 2 ** min(current.attempts, 12))
                    )
    return published
