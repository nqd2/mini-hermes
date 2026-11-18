from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.documents import resource_indexing, worker
from weai.documents.models import (
    Document,
    DocumentChunk,
    Folder,
    Preview,
    ResourceIndexJob,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
    now,
)
from weai.documents.revision_service import (
    commit_preview,
    dispatch_pending_outbox,
    prepare_rollback_preview,
)
from weai.documents.service import get_verified_revision_content


class Store:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def put(self, key: str, content: bytes, mime_type: str) -> None:
        self.objects[key] = content

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def _patch_hash(base_revision: int, base_sha256: str, operations: list[dict], artifact_sha256: str) -> str:
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


async def _setup(tmp_path, monkeypatch, *, preview_count: int = 1):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "revision-publication.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[
                    Folder.__table__,
                    Document.__table__,
                    Revision.__table__,
                    Preview.__table__,
                    RevisionApproval.__table__,
                    RevisionOutbox.__table__,
                    SyncJob.__table__,
                    ResourceIndexJob.__table__,
                    DocumentChunk.__table__,
                ],
            )
        )
    monkeypatch.setattr(resource_indexing, 'resource_index_worker_enabled', lambda: True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    base = b'original document'
    base_sha256 = hashlib.sha256(base).hexdigest()
    doc = Document(
        id='doc-1', owner_id='owner-1', filename='report.txt', mime_type='text/plain', current_revision=1
    )
    store_objects = {'documents/doc-1/revisions/one.txt': base}
    previews = []
    for ordinal in range(preview_count):
        artifact = f'proposed revision {ordinal}'.encode()
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        preview_id = f'preview-{ordinal}'
        key = f'documents/doc-1/staged/previews/{preview_id}/{artifact_sha256}'
        operations = [{'op': 'replace_text', 'find': 'original', 'replace': f'proposed-{ordinal}'}]
        patch_hash = _patch_hash(1, base_sha256, operations, artifact_sha256)
        preview = Preview(
            id=preview_id,
            document_id=doc.id,
            owner_id='owner-1',
            base_revision=1,
            base_sha256=base_sha256,
            artifact_sha256=artifact_sha256,
            patch_hash=patch_hash,
            operations=operations,
            status='ready',
            preview_key=key,
        )
        previews.append((preview, artifact))
        store_objects[key] = artifact

    async with sessions() as db:
        db.add(doc)
        db.add(Revision(
            id='revision-1', document_id=doc.id, number=1,
            object_key='documents/doc-1/revisions/one.txt', sha256=base_sha256, size=len(base),
        ))
        db.add_all(preview for preview, _ in previews)
        await db.commit()
    return engine, sessions, Store(store_objects), previews


def _approval(preview: Preview) -> dict:
    return {
        'base_revision': preview.base_revision,
        'base_sha256': preview.base_sha256,
        'artifact_sha256': preview.artifact_sha256,
        'patch_hash': preview.patch_hash,
    }


@pytest.mark.asyncio
async def test_commit_publishes_revision_approval_and_outbox_atomically_and_retries_idempotently(
    tmp_path, monkeypatch
):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    try:
        async with sessions() as db:
            revision, _ = await commit_preview(
                db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='commit-1'
            )
            first_revision_id = revision.id
        async with sessions() as db:
            retry, _ = await commit_preview(
                db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='commit-1'
            )
            current = await db.get(Document, 'doc-1')
            approvals = list((await db.scalars(select(RevisionApproval))).all())
            outbox = list((await db.scalars(select(RevisionOutbox))).all())
            revisions = list((await db.scalars(select(Revision))).all())

        assert retry.id == first_revision_id
        assert current.current_revision == 2
        assert len(revisions) == 2
        assert len(approvals) == 1
        assert approvals[0].user_id == 'owner-1'
        assert approvals[0].resource_id == 'doc-1'
        assert approvals[0].base_revision == 1
        assert approvals[0].patch_hash == preview.patch_hash
        assert approvals[0].approver_id == 'owner-1'
        assert approvals[0].expires_at > approvals[0].created_at
        assert approvals[0].consumed_at is not None
        assert len(outbox) == 1
        assert outbox[0].revision_id == first_revision_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_preview_approval_is_rejected_without_publication(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    preview.created_at = now() - dt.timedelta(minutes=16)
    try:
        async with sessions() as db:
            persisted_preview = await db.get(Preview, preview.id)
            persisted_preview.created_at = preview.created_at
            await db.commit()

        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db,
                    store,
                    'owner-1',
                    preview.id,
                    approval=_approval(preview),
                    idempotency_key='expired-commit',
                )
            assert error.value.status_code == 409

        async with sessions() as db:
            document = await db.get(Document, 'doc-1')
            revisions = list((await db.scalars(select(Revision))).all())
            approvals = list((await db.scalars(select(RevisionApproval))).all())
            outbox = list((await db.scalars(select(RevisionOutbox))).all())
        assert document.current_revision == 1
        assert len(revisions) == 1
        assert approvals == []
        assert outbox == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_publication_transaction_keeps_revision_invisible_and_retryable(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]

    def fail_publication_commit(_session):
        raise RuntimeError('simulated database commit failure')

    try:
        async with sessions() as db:
            event.listen(db.sync_session, 'before_commit', fail_publication_commit, once=True)
            with pytest.raises(RuntimeError, match='simulated database commit failure'):
                await commit_preview(
                    db,
                    store,
                    'owner-1',
                    preview.id,
                    approval=_approval(preview),
                    idempotency_key='recoverable-commit',
                )
            await db.rollback()

        async with sessions() as db:
            document = await db.get(Document, 'doc-1')
            revisions = list((await db.scalars(select(Revision))).all())
            approvals = list((await db.scalars(select(RevisionApproval))).all())
            outbox = list((await db.scalars(select(RevisionOutbox))).all())
            persisted_preview = await db.get(Preview, preview.id)
        assert document.current_revision == 1
        assert len(revisions) == 1
        assert approvals == []
        assert outbox == []
        assert persisted_preview.status == 'ready'
        assert store.objects[preview.preview_key] == previews[0][1]

        async with sessions() as db:
            recovered, _ = await commit_preview(
                db,
                store,
                'owner-1',
                preview.id,
                approval=_approval(preview),
                idempotency_key='recoverable-commit',
            )
        assert recovered.number == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_abandoned_preview_cleanup_preserves_committed_revision_object(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch, preview_count=2)
    committed, abandoned = previews
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: store)
    try:
        async with sessions() as db:
            await commit_preview(
                db,
                store,
                'owner-1',
                committed[0].id,
                approval=_approval(committed[0]),
                idempotency_key='cleanup-committed',
            )
            stale_preview = await db.get(Preview, abandoned[0].id)
            stale_preview.created_at = now() - dt.timedelta(days=8)
            await db.commit()

        removed = await worker.cleanup_abandoned_previews(retention_days=7)

        async with sessions() as db:
            committed_preview = await db.get(Preview, committed[0].id)
            stale_preview = await db.get(Preview, abandoned[0].id)
            revision = await db.scalar(select(Revision).where(Revision.preview_id == committed[0].id))
        assert removed == 1
        assert committed_preview.status == 'committed'
        assert stale_preview is None
        assert committed[0].preview_key in store.objects
        assert abandoned[0].preview_key not in store.objects
        assert store.objects[revision.object_key] == committed[1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_outbox_replays_pending_event_with_stable_id(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    sent = []

    async def capture_event(_request, event):
        sent.append(event.id)

    import weai.events

    monkeypatch.setattr(weai.events, 'publish_event', capture_event)
    request = type(
        'Request',
        (),
        {'app': type('App', (), {'state': type('State', (), {'instance_id': 'tests'})()})()},
    )()
    try:
        async with sessions() as db:
            revision, _ = await commit_preview(
                db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='outbox-commit'
            )
            await dispatch_pending_outbox(db, request, revision.id)
            await dispatch_pending_outbox(db, request, revision.id)
            outbox = await db.scalar(select(RevisionOutbox).where(RevisionOutbox.revision_id == revision.id))

        assert sent == [outbox.event_key]
        assert outbox.published_at is not None
        assert outbox.lease_until is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_revision_read_fails_closed_on_missing_checksum_integrity(tmp_path, monkeypatch):
    engine, sessions, store, _ = await _setup(tmp_path, monkeypatch)
    try:
        async with sessions() as db:
            revision = await db.scalar(select(Revision).where(Revision.number == 1))
        store.objects[revision.object_key] = b'corrupt committed bytes'
        with pytest.raises(HTTPException) as error:
            await get_verified_revision_content(store, revision)
        assert error.value.status_code == 503
        assert error.value.detail['code'] == 'document_revision_integrity_failure'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_creates_approved_compensating_revision_and_retains_history(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    first_preview, _ = previews[0]
    try:
        async with sessions() as db:
            second_revision, _ = await commit_preview(
                db,
                store,
                'owner-1',
                first_preview.id,
                approval=_approval(first_preview),
                idempotency_key='commit-before-rollback',
            )
            doc = await db.get(Document, 'doc-1')
            rollback = await prepare_rollback_preview(
                db,
                store,
                'owner-1',
                doc.id,
                target_revision=1,
                expected_current_revision=2,
            )
            assert rollback.base_revision == 2
            assert rollback.status == 'ready', rollback.error
            assert rollback.operations == [{'op': 'rollback', 'target_revision': 1}]
            approval = {
                'base_revision': rollback.base_revision,
                'base_sha256': rollback.base_sha256,
                'artifact_sha256': rollback.artifact_sha256,
                'patch_hash': rollback.patch_hash,
            }
            compensating, _ = await commit_preview(
                db,
                store,
                'owner-1',
                rollback.id,
                approval=approval,
                idempotency_key='rollback-to-revision-1',
            )
            retained = list((await db.scalars(select(Revision).order_by(Revision.number))).all())

        assert second_revision.number == 2
        assert compensating.number == 3
        assert len(retained) == 3
        assert retained[0].object_key != retained[2].object_key
        assert store.objects[compensating.object_key] == b'original document'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_rejects_a_different_approved_patch_without_publishing(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    try:
        approval = _approval(preview)
        approval['patch_hash'] = '0' * 64
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(db, store, 'owner-1', preview.id, approval=approval, idempotency_key='commit-2')
            await db.rollback()
        async with sessions() as db:
            current = await db.get(Document, 'doc-1')
            assert current.current_revision == 1
            assert await db.scalar(select(RevisionApproval.id)) is None
            assert await db.scalar(select(RevisionOutbox.id)) is None
        assert error.value.status_code == 400
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_commit_or_observe_revision_approval(tmp_path, monkeypatch):
    from weai.internal import db as internal_db
    from weai.models.access_grants import AccessGrant
    from weai.models.groups import Group, GroupMember

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    try:
        async with engine.begin() as connection:
            for table in (Group.__table__, GroupMember.__table__, AccessGrant.__table__):
                await connection.run_sync(table.create)
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'intruder', preview.id, approval=_approval(preview), idempotency_key='intruder-commit'
                )
            await db.rollback()
        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 1
            assert await db.scalar(select(RevisionApproval.id)) is None
            assert await db.scalar(select(RevisionOutbox.id)) is None
        assert error.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checksum_mismatch_leaves_revision_unpublished(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    store.objects[preview.preview_key] = b'corrupted after preview'
    try:
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='bad-storage'
                )
            await db.rollback()
        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 1
            assert await db.scalar(select(RevisionApproval.id)) is None
            assert await db.scalar(select(RevisionOutbox.id)) is None
        assert error.value.status_code == 409
        assert 'checksum' in str(error.value.detail).lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_ready_preview_gets_patch_hash_before_approval(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    preview.patch_hash = None
    expected_patch_hash = _patch_hash(
        preview.base_revision, preview.base_sha256, preview.operations, preview.artifact_sha256
    )
    try:
        async with sessions() as db:
            stored = await db.get(Preview, preview.id)
            stored.patch_hash = None
            await db.commit()
        async with sessions() as db:
            stored = await db.get(Preview, preview.id)
            revision, _ = await commit_preview(
                db,
                store,
                'owner-1',
                preview.id,
                approval={**_approval(preview), 'patch_hash': expected_patch_hash},
                idempotency_key='legacy-preview',
            )
            approval = await db.scalar(select(RevisionApproval).where(RevisionApproval.preview_id == preview.id))

        assert revision.number == 2
        assert approval.patch_hash == _patch_hash(
            preview.base_revision, preview.base_sha256, preview.operations, preview.artifact_sha256
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_proposals_based_on_old_revision_cannot_both_commit(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch, preview_count=2)
    first, second = previews[0][0], previews[1][0]
    try:
        async with sessions() as db:
            await commit_preview(db, store, 'owner-1', first.id, approval=_approval(first), idempotency_key='commit-a')
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'owner-1', second.id, approval=_approval(second), idempotency_key='commit-b'
                )
            await db.rollback()
        assert error.value.status_code == 409
        async with sessions() as db:
            current = await db.get(Document, 'doc-1')
            assert current.current_revision == 2
            assert len(list((await db.scalars(select(Revision))).all())) == 2
            assert len(list((await db.scalars(select(RevisionOutbox))).all())) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_preview_replay_rechecks_current_write_permission(tmp_path, monkeypatch):
    from weai.documents import library

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    try:
        async with sessions() as db:
            first, _ = await commit_preview(
                db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='same-key'
            )
            revision_id = first.id

        checked = []

        async def revoke(_db, document, user_id, permission, user_group_ids=None, is_admin=False):
            checked.append((document.id, user_id, permission, is_admin))
            return False

        monkeypatch.setattr(library, 'has_document_access', revoke)
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'owner-1', preview.id, approval=_approval(preview), idempotency_key='same-key'
                )
            assert error.value.status_code == 404
        assert checked == [('doc-1', 'owner-1', 'write', False)]

        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 2
            assert (await db.scalar(select(Revision).where(Revision.id == revision_id))) is not None
            assert len(list((await db.scalars(select(RevisionApproval))).all())) == 1
            assert len(list((await db.scalars(select(RevisionOutbox))).all())) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_key_cannot_be_reused_for_another_preview(tmp_path, monkeypatch):
    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch, preview_count=2)
    first, second = previews[0][0], previews[1][0]
    try:
        async with sessions() as db:
            await commit_preview(db, store, 'owner-1', first.id, approval=_approval(first), idempotency_key='same-key')
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'owner-1', second.id, approval=_approval(second), idempotency_key='same-key'
                )
            assert error.value.status_code == 409
            assert error.value.detail['code'] == 'idempotency_key_reused'
        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 2
            assert len(list((await db.scalars(select(Revision))).all())) == 2
            assert len(list((await db.scalars(select(RevisionApproval))).all())) == 1
            assert len(list((await db.scalars(select(RevisionOutbox))).all())) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_replay_denies_collaborator_after_write_grant_is_revoked(tmp_path, monkeypatch):
    from sqlalchemy import delete
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.models.access_grants import AccessGrant

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    # The production DB helper otherwise opens its global engine, outside this isolated fixture.
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AccessGrant.__table__.create)
        async with sessions() as db:
            db.add(AccessGrant(
                id='grant-1', resource_type='ar_document', resource_id='doc-1',
                principal_type='user', principal_id='collaborator', permission='write', created_at=1,
            ))
            await db.commit()
        async with sessions() as db:
            committed, _ = await commit_preview(
                db, store, 'collaborator', preview.id, approval=_approval(preview), idempotency_key='grant-key'
            )
            assert committed.number == 2
        async with sessions() as db:
            await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))
            await db.commit()
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(
                    db, store, 'collaborator', preview.id, approval=_approval(preview), idempotency_key='grant-key'
                )
            assert error.value.status_code == 404
        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 2
            assert len(list((await db.scalars(select(Revision))).all())) == 2
            assert len(list((await db.scalars(select(RevisionApproval))).all())) == 1
            assert len(list((await db.scalars(select(RevisionOutbox))).all())) == 1
    finally:
        await engine.dispose()


def test_patch_hash_binds_unicode_content_and_operation_order():
    from weai.documents.revision_integrity import compute_patch_hash

    common = {'base_revision': 1, 'base_sha256': 'a' * 64, 'artifact_sha256': 'b' * 64}
    operations = [
        {'op': 'set_cell', 'sheet': 'Khảo sát', 'cell': 'A1', 'value': 'dòng 1\ndòng 2'},
        {'op': 'set_cell', 'sheet': 'Khảo sát', 'cell': 'A2', 'value': 'câu trả lời'},
    ]
    reordered_keys = [dict(reversed(list(item.items()))) for item in operations]
    original = compute_patch_hash(**common, operations=operations)
    assert original == compute_patch_hash(**common, operations=reordered_keys)
    assert original != compute_patch_hash(**common, operations=list(reversed(operations)))
    assert original != compute_patch_hash(**{**common, 'base_revision': 2}, operations=operations)
    assert original != compute_patch_hash(**{**common, 'artifact_sha256': 'c' * 64}, operations=operations)


@pytest.mark.asyncio
@pytest.mark.parametrize('worker_enabled', [True, False], ids=['queued-index', 'inline-index'])
async def test_approval_expiring_during_index_prevents_publication(tmp_path, monkeypatch, worker_enabled):
    """An approval cannot authorize publication after expiring during async work."""
    from weai.documents import revision_service

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    preview_created = preview.created_at.replace(tzinfo=dt.UTC) if preview.created_at.tzinfo is None else preview.created_at
    expiry = preview_created + dt.timedelta(minutes=15)
    current_time = [expiry - dt.timedelta(seconds=1)]
    monkeypatch.setattr(revision_service, 'now', lambda: current_time[0])
    from weai.documents import chunks

    monkeypatch.setattr(resource_indexing, 'resource_index_worker_enabled', lambda: worker_enabled)
    target_module = resource_indexing if worker_enabled else chunks
    function_name = 'enqueue_resource_index_job' if worker_enabled else 'replace_document_chunks'
    original_index = getattr(target_module, function_name)

    async def index_after_expiry(*args, **kwargs):
        result = await original_index(*args, **kwargs)
        current_time[0] = expiry
        return result

    monkeypatch.setattr(target_module, function_name, index_after_expiry)
    try:
        async with sessions() as db:
            with pytest.raises(HTTPException) as error:
                await commit_preview(db, store, 'owner-1', preview.id, approval=_approval(preview),
                                     idempotency_key='expires-during-index')
            assert error.value.status_code == 409
            # Even if the caller later commits this session, the failed publication
            # must not persist any of its flushed or pending records.
            await db.commit()
        async with sessions() as db:
            assert (await db.get(Document, 'doc-1')).current_revision == 1
            assert len(list((await db.scalars(select(Revision))).all())) == 1
            assert list((await db.scalars(select(RevisionApproval))).all()) == []
            assert list((await db.scalars(select(RevisionOutbox))).all()) == []
            assert list((await db.scalars(select(ResourceIndexJob))).all()) == []
            assert list((await db.scalars(select(DocumentChunk))).all()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_metadata_rejects_creator_after_document_grant_revoked(tmp_path, monkeypatch):
    """A preview creator loses metadata access when document access is revoked."""
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    from weai.internal import db as internal_db
    from weai.documents import library, routes
    from weai.models.access_grants import AccessGrant, AccessGrants

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    monkeypatch.setattr(routes, 'ObjectStore', lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='collaborator', role='user')

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[routes.get_async_session] = session_override
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AccessGrant.__table__.create)
        async with sessions() as db:
            row = await db.get(Preview, preview.id)
            row.owner_id = 'collaborator'
            row.diff = 'confidential document content'
            db.add(AccessGrant(id='grant-1', resource_type='ar_document', resource_id='doc-1',
                               principal_type='user', principal_id='collaborator', permission='read', created_at=1))
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = f'/api/v1/agent-runtime/previews/{preview.id}'
            authorized = await client.get(path)
            assert authorized.status_code == 200
            assert authorized.json()['diff'] == 'confidential document content'
            async with sessions() as db:
                await AccessGrants.set_access_grants('ar_document', 'doc-1', [], db=db)
            revoked = await client.get(path)
            assert revoked.status_code == 404
            assert 'confidential document content' not in revoked.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_preview_list_hides_document_after_grant_revoked(tmp_path, monkeypatch):
    """Chat attachment metadata does not override current document access."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from fastapi import FastAPI
    from weai.internal import db as internal_db
    from weai.documents import library, routes
    from weai.documents.models import ChatResource
    from weai.models.access_grants import AccessGrant, AccessGrants

    engine, sessions, _, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    monkeypatch.setattr(routes.Chats, 'get_chat_by_id_and_user_id', AsyncMock(return_value=object()))
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='collaborator', role='user')

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[routes.get_async_session] = session_override
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AccessGrant.__table__.create)
            await connection.run_sync(ChatResource.__table__.create)
        async with sessions() as db:
            row = await db.get(Preview, preview.id)
            row.owner_id = 'collaborator'
            row.diff = 'confidential document content'
            db.add(ChatResource(id='association', chat_id='chat-1', document_id='doc-1', granted_by='owner-1'))
            db.add(AccessGrant(id='grant-1', resource_type='ar_document', resource_id='doc-1',
                               principal_type='user', principal_id='collaborator', permission='read', created_at=1))
            db.add(Document(id='other-doc', owner_id='other-owner', filename='other.txt',
                            mime_type='text/plain', current_revision=1))
            db.add(ChatResource(id='other-association', chat_id='chat-1', document_id='other-doc',
                                granted_by='other-owner'))
            for ordinal in range(11):
                db.add(Preview(id=f'other-preview-{ordinal}', document_id='other-doc', owner_id='collaborator',
                               base_revision=1, operations=[], status='ready',
                               created_at=now() + dt.timedelta(minutes=ordinal + 1)))
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = '/api/v1/agent-runtime/chats/chat-1/previews'
            authorized = await client.get(path)
            assert authorized.status_code == 200
            assert [item['id'] for item in authorized.json()] == [preview.id]
            assert authorized.json()[0]['diff'] == 'confidential document content'
            async with sessions() as db:
                await AccessGrants.set_access_grants('ar_document', 'doc-1', [], db=db)
            revoked = await client.get(path)
            assert revoked.status_code == 200
            assert revoked.json() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_routes_recheck_inherited_group_folder_access(tmp_path, monkeypatch):
    """Removing group membership removes inherited folder access to previews."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import delete
    from weai.internal import db as internal_db
    from weai.documents import routes
    from weai.documents.models import ChatResource
    from weai.models.access_grants import AccessGrant
    from weai.models.groups import Group, GroupMember

    engine, sessions, store, previews = await _setup(tmp_path, monkeypatch)
    preview, _ = previews[0]
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(routes.Chats, 'get_chat_by_id_and_user_id', AsyncMock(return_value=object()))
    monkeypatch.setattr(routes, 'ObjectStore', lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='collaborator', role='user')

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[routes.get_async_session] = session_override
    try:
        async with engine.begin() as connection:
            for table in (Group.__table__, GroupMember.__table__, AccessGrant.__table__, ChatResource.__table__):
                await connection.run_sync(table.create)
        async with sessions() as db:
            db.add(Folder(id='shared-folder', owner_id='owner-1', name='Shared'))
            (await db.get(Document, 'doc-1')).folder_id = 'shared-folder'
            row = await db.get(Preview, preview.id)
            row.owner_id = 'collaborator'
            row.diff = 'private inherited content'
            db.add_all([
                Group(id='parent', user_id='owner-1', name='Parent', description='', created_at=1, updated_at=1),
                Group(id='child', user_id='owner-1', name='Child', description='', parent_group_id='parent',
                      created_at=1, updated_at=1),
                GroupMember(id='membership', group_id='child', user_id='collaborator'),
                AccessGrant(id='folder-grant', resource_type='ar_folder', resource_id='shared-folder',
                            principal_type='group', principal_id='parent', permission='read', created_at=1),
                ChatResource(id='association', chat_id='chat-1', document_id='doc-1', granted_by='owner-1'),
            ])
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            detail_path = f'/api/v1/agent-runtime/previews/{preview.id}'
            list_path = '/api/v1/agent-runtime/chats/chat-1/previews'
            assert (await client.get(detail_path)).status_code == 200
            assert len((await client.get(list_path)).json()) == 1
            async with sessions() as db:
                await db.execute(delete(GroupMember).where(GroupMember.id == 'membership'))
                await db.commit()
            assert (await client.get(detail_path)).status_code == 404
            assert (await client.get(list_path)).json() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'checksum', 'size'])
async def test_document_content_route_fails_closed_on_committed_object_integrity_failure(
    tmp_path, monkeypatch, caplog, failure
):
    """The HTTP document reader must never return corrupt committed bytes."""
    from types import SimpleNamespace
    import logging
    import httpx
    from fastapi import FastAPI
    from weai.documents import routes

    engine, sessions, store, _ = await _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, 'ObjectStore', lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='owner-1', role='user')

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[routes.get_async_session] = session_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = '/api/v1/agent-runtime/documents/doc-1/content?revision=1'
            good = await client.get(path)
            assert good.status_code == 200
            assert good.content == b'original document'
            assert good.headers['content-type'].startswith('text/plain')
            assert 'report.txt' in good.headers['content-disposition']
            key = 'documents/doc-1/revisions/one.txt'
            if failure == 'missing':
                del store.objects[key]
            elif failure == 'checksum':
                store.objects[key] = b'x' * len(store.objects[key])
            else:
                async with sessions() as db:
                    revision = await db.get(Revision, 'revision-1')
                    revision.size += 1
                    await db.commit()
            caplog.clear()
            integrity_logger = logging.getLogger('weai.documents.revision_integrity')
            integrity_logger.addHandler(caplog.handler)
            try:
                denied = await client.get(path)
            finally:
                integrity_logger.removeHandler(caplog.handler)
            assert denied.status_code == 503
            assert denied.json()['detail']['code'] == 'document_revision_integrity_failure'
            assert b'original document' not in denied.content
            assert any(record.levelname == 'CRITICAL' and 'revision' in record.getMessage().lower()
                       for record in caplog.records)
    finally:
        await engine.dispose()
