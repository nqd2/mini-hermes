"""PostgreSQL integration coverage for resource index/task locking and races.

Set ``MH_TEST_POSTGRES_URL`` to an isolated, disposable PostgreSQL database URL
to enable these tests. Each test creates and drops its own random schema.
"""

import asyncio
import datetime as dt
import hashlib
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import (
    ChatResource,
    Document,
    DocumentChunk,
    Folder,
    Preview,
    ResourceIndexJob,
    ResourceTask,
    Revision,
    SyncJob,
    now,
)
from open_webui.mini_hermes.resource_indexing import enqueue_missing_resource_index_jobs


@pytest_asyncio.fixture
async def postgres_resource_db():
    database_url = os.getenv('MH_TEST_POSTGRES_URL')
    if not database_url:
        pytest.skip('Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests')

    url = make_url(database_url)
    if url.drivername == 'postgresql':
        url = url.set(drivername='postgresql+psycopg')
    schema = f'mh_test_{uuid4().hex}'
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    test_engine = None
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        isolated_url = url.set(query={**dict(url.query), 'options': f'-csearch_path={schema}'})
        test_engine = create_async_engine(isolated_url, pool_pre_ping=True)
        async with test_engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Document.metadata.create_all(
                    sync,
                    tables=[
                        Folder.__table__,
                        ChatResource.__table__,
                        Document.__table__,
                        Revision.__table__,
                        DocumentChunk.__table__,
                        ResourceIndexJob.__table__,
                        ResourceTask.__table__,
                        Preview.__table__,
                        SyncJob.__table__,
                    ],
                )
            )

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def _seed_pending_jobs(session_factory, count: int = 2) -> list[str]:
    created_at = now()
    ids = [f'pg-index-job-{index}' for index in range(count)]
    async with session_factory() as db:
        for index, job_id in enumerate(ids):
            document_id = f'pg-index-document-{index}'
            db.add(
                Document(
                    id=document_id,
                    owner_id='postgres-test',
                    filename=f'report-{index}.txt',
                    mime_type='text/plain',
                    current_revision=1,
                )
            )
            db.add(
                ResourceIndexJob(
                    id=job_id,
                    document_id=document_id,
                    revision=1,
                    status='pending',
                    attempts=0,
                    next_attempt_at=created_at - dt.timedelta(seconds=1),
                    created_at=created_at + dt.timedelta(seconds=index),
                    updated_at=created_at,
                )
            )
        await db.commit()
    return ids


async def _seed_pending_tasks(session_factory, count: int = 2) -> list[str]:
    created_at = now()
    ids = [f'pg-resource-task-{index}' for index in range(count)]
    async with session_factory() as db:
        for index, task_id in enumerate(ids):
            document_id = f'pg-task-document-{index}'
            db.add(
                Document(
                    id=document_id,
                    owner_id='postgres-test',
                    filename=f'task-{index}.txt',
                    mime_type='text/plain',
                    current_revision=1,
                )
            )
            db.add(
                ResourceTask(
                    id=task_id,
                    document_id=document_id,
                    owner_id='postgres-test',
                    chat_id=f'postgres-chat-{index}',
                    revision=1,
                    created_at=created_at + dt.timedelta(seconds=index),
                )
            )
        await db.commit()
    return ids


@pytest.mark.asyncio
async def test_index_worker_skips_a_locked_job(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    first_id, second_id = await _seed_pending_jobs(postgres_resource_db)

    async with postgres_resource_db() as lock_session:
        async with lock_session.begin():
            locked = (
                await lock_session.execute(
                    select(ResourceIndexJob).where(ResourceIndexJob.id == first_id).with_for_update()
                )
            ).scalar_one()
            assert locked.status == 'pending'

            claimed_id = await worker.claim_resource_index_job()

            assert claimed_id == second_id

    async with postgres_resource_db() as db:
        first = await db.get(ResourceIndexJob, first_id)
        second = await db.get(ResourceIndexJob, second_id)
        assert first.status == 'pending'
        assert second.status == 'processing'
        assert second.attempts == 1


@pytest.mark.asyncio
async def test_parallel_index_workers_claim_distinct_jobs(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    expected_ids = set(await _seed_pending_jobs(postgres_resource_db))

    claimed_ids = await asyncio.gather(
        worker.claim_resource_index_job(),
        worker.claim_resource_index_job(),
    )

    assert set(claimed_ids) == expected_ids
    async with postgres_resource_db() as db:
        claimed = (
            (await db.execute(select(ResourceIndexJob).where(ResourceIndexJob.id.in_(claimed_ids)))).scalars().all()
        )
        assert len(claimed) == 2
        assert all(job.status == 'processing' and job.attempts == 1 for job in claimed)


@pytest.mark.asyncio
async def test_index_worker_reclaims_an_expired_lease(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    (job_id,) = await _seed_pending_jobs(postgres_resource_db, count=1)
    async with postgres_resource_db() as db:
        job = await db.get(ResourceIndexJob, job_id)
        job.status = 'processing'
        job.attempts = 1
        job.lease_until = now() - dt.timedelta(minutes=1)
        await db.commit()

    assert await worker.claim_resource_index_job() == job_id
    async with postgres_resource_db() as db:
        job = await db.get(ResourceIndexJob, job_id)
        lease_until = job.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=dt.UTC)
        assert job.status == 'processing'
        assert job.attempts == 2
        assert lease_until > now()


@pytest.mark.asyncio
async def test_parallel_legacy_backfills_preserve_one_job_per_revision(postgres_resource_db):
    document_id = 'pg-legacy-document'
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='legacy.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            Revision(
                id='pg-legacy-revision',
                document_id=document_id,
                number=1,
                object_key='legacy-object',
                sha256='legacy-hash',
                size=10,
            )
        )
        await db.commit()

    async def backfill_once():
        async with postgres_resource_db() as db:
            return await enqueue_missing_resource_index_jobs(db, limit=10)

    await asyncio.gather(backfill_once(), backfill_once())

    async with postgres_resource_db() as db:
        jobs = (
            (
                await db.execute(
                    select(ResourceIndexJob).where(
                        ResourceIndexJob.document_id == document_id,
                        ResourceIndexJob.revision == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == 'pending'


@pytest.mark.asyncio
async def test_resource_task_worker_skips_a_locked_task(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    first_id, second_id = await _seed_pending_tasks(postgres_resource_db)

    async with postgres_resource_db() as lock_session:
        async with lock_session.begin():
            locked = (
                await lock_session.execute(select(ResourceTask).where(ResourceTask.id == first_id).with_for_update())
            ).scalar_one()
            assert locked.status == 'pending'

            claimed_id = await worker.claim_resource_task()

            assert claimed_id == second_id

    async with postgres_resource_db() as db:
        first = await db.get(ResourceTask, first_id)
        second = await db.get(ResourceTask, second_id)
        assert first.status == 'pending'
        assert second.status == 'processing'
        assert second.lease_until is not None


@pytest.mark.asyncio
async def test_resource_task_worker_reclaims_an_expired_lease(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    (task_id,) = await _seed_pending_tasks(postgres_resource_db, count=1)
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        task.status = 'processing'
        task.lease_until = now() - dt.timedelta(minutes=1)
        await db.commit()

    assert await worker.claim_resource_task() == task_id
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        lease_until = task.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=dt.UTC)
        assert task.status == 'processing'
        assert lease_until > now()


@pytest.mark.asyncio
async def test_resource_task_cancel_and_resume_commit_in_postgres(postgres_resource_db):
    from open_webui.mini_hermes.resource_tasks import request_task_cancel, resume_summary_task

    document_id = 'pg-cancel-resume-document'
    task_id = 'pg-cancel-resume-task'
    async with postgres_resource_db() as db:
        document = Document(
            id=document_id,
            owner_id='postgres-test',
            filename='cancel-resume.txt',
            mime_type='text/plain',
            current_revision=1,
        )
        db.add(document)
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                revision=1,
                status='pending',
            )
        )
        await db.commit()

        cancelled = await request_task_cancel(db, task_id, 'postgres-test')
        assert cancelled.status == 'cancelled'
        assert cancelled.cancel_requested is True

    async with postgres_resource_db() as db:
        cancelled = await db.get(ResourceTask, task_id)
        document = await db.get(Document, document_id)
        assert cancelled.status == 'cancelled'
        assert cancelled.cancel_requested is True

        resumed = await resume_summary_task(db, task_id, 'postgres-test', document)
        assert resumed.status == 'pending'
        assert resumed.cancel_requested is False


@pytest.mark.asyncio
async def test_resource_task_checkpoint_and_revision_resume_validation_in_postgres(postgres_resource_db):
    from fastapi import HTTPException

    from open_webui.mini_hermes.resource_tasks import request_task_cancel, resume_summary_task

    document_id = 'pg-resume-checkpoint-document'
    task_id = 'pg-resume-checkpoint-task'
    checkpoint = {
        'items': [{'summary': 'saved section', 'evidence_ids': ['stable-chunk-id']}],
        'reduced': [],
    }
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='checkpoint.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                revision=1,
                status='pending',
                checkpoint=checkpoint,
                next_ordinal=7,
                completed_units=7,
            )
        )
        await db.commit()
        await request_task_cancel(db, task_id, 'postgres-test')

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        resumed = await resume_summary_task(db, task_id, 'postgres-test', document)
        assert resumed.status == 'pending'
        assert resumed.next_ordinal == 7
        assert resumed.completed_units == 7
        assert resumed.checkpoint == checkpoint

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        await request_task_cancel(db, task_id, 'postgres-test')
        document.current_revision = 2
        await db.commit()

        with pytest.raises(HTTPException) as error:
            await resume_summary_task(db, task_id, 'postgres-test', document)
        assert error.value.status_code == 409
        assert 'revision changed' in error.value.detail.lower()


@pytest.mark.asyncio
async def test_resource_task_resume_rechecks_revoked_chat_acl_in_postgres(postgres_resource_db, monkeypatch):
    from fastapi import HTTPException

    from open_webui.mini_hermes import tools
    from open_webui.mini_hermes.library import set_chat_document_access

    document_id = 'pg-acl-resume-document'
    task_id = 'pg-acl-resume-task'
    chat_id = 'pg-acl-resume-chat'
    async with postgres_resource_db() as db:
        document = Document(
            id=document_id,
            owner_id='postgres-test',
            filename='acl.txt',
            mime_type='text/plain',
            current_revision=1,
        )
        db.add(document)
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                chat_id=chat_id,
                revision=1,
                status='cancelled',
                checkpoint={'items': [], 'reduced': []},
            )
        )
        await db.commit()
        await set_chat_document_access(db, chat_id, 'postgres-test', document_id, True)
        await set_chat_document_access(db, chat_id, 'postgres-test', document_id, False)

    @asynccontextmanager
    async def open_db():
        async with postgres_resource_db() as db:
            yield db

    monkeypatch.setattr(tools, 'get_async_db', open_db)

    with pytest.raises(HTTPException) as error:
        await tools.resume_document_task(
            task_id,
            __user__={'id': 'postgres-test', 'role': 'user'},
            __files__=[],
            __chat_id__=chat_id,
        )
    assert error.value.status_code == 404
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        assert task.status == 'cancelled'


@pytest.mark.asyncio
async def test_preview_commit_publishes_only_verified_staged_artifacts_in_postgres(postgres_resource_db, monkeypatch):
    from open_webui.mini_hermes.service import commit_preview

    monkeypatch.setenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    document_id = 'pg-staged-commit-document'
    preview_id = 'pg-staged-commit-preview'
    base = b'original artifact'
    edited = b'approved staged artifact'
    base_hash = hashlib.sha256(base).hexdigest()
    edited_hash = hashlib.sha256(edited).hexdigest()

    class MemoryStore:
        def __init__(self):
            self.objects = {'base-key': base, 'staged-key': edited}

        async def get(self, key):
            if key not in self.objects:
                raise OSError('staged object storage unavailable')
            return self.objects[key]

    store = MemoryStore()
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='staged.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            Revision(
                id='pg-staged-base-revision',
                document_id=document_id,
                number=1,
                object_key='base-key',
                sha256=base_hash,
                size=len(base),
            )
        )
        db.add(
            Preview(
                id=preview_id,
                document_id=document_id,
                owner_id='postgres-test',
                base_revision=1,
                base_sha256=base_hash,
                artifact_sha256=edited_hash,
                operations=[],
                status='ready',
                preview_key='staged-key',
            )
        )
        await db.commit()

        approval = {
            'base_revision': 1,
            'base_sha256': base_hash,
            'artifact_sha256': edited_hash,
        }
        revision, _ = await commit_preview(db, store, 'postgres-test', preview_id, approval=approval)
        assert revision.number == 2
        assert revision.object_key == 'staged-key'
        assert store.objects['staged-key'] == edited

        retry_revision, _ = await commit_preview(db, store, 'postgres-test', preview_id)
        assert retry_revision.id == revision.id

    missing_preview_id = 'pg-missing-staged-preview'
    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        db.add(
            Preview(
                id=missing_preview_id,
                document_id=document_id,
                owner_id='postgres-test',
                base_revision=2,
                base_sha256=edited_hash,
                artifact_sha256=hashlib.sha256(b'not-present').hexdigest(),
                operations=[],
                status='ready',
                preview_key='missing-staged-key',
            )
        )
        await db.commit()
        with pytest.raises(OSError, match='staged object storage unavailable'):
            await commit_preview(
                db,
                store,
                'postgres-test',
                missing_preview_id,
                approval={
                    'base_revision': 2,
                    'base_sha256': edited_hash,
                    'artifact_sha256': hashlib.sha256(b'not-present').hexdigest(),
                },
            )
        await db.rollback()

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        missing_preview = await db.get(Preview, missing_preview_id)
        revisions = (await db.execute(select(Revision).where(Revision.document_id == document_id))).scalars().all()
        assert document.current_revision == 2
        assert missing_preview.status == 'ready'
        assert sorted(revision.number for revision in revisions) == [1, 2]
