"""PostgreSQL integration coverage for resource index/task locking and races.

Set ``MH_TEST_POSTGRES_URL`` to an isolated, disposable PostgreSQL database URL
to enable these tests. Each test creates and drops its own random schema.
"""

import asyncio
import datetime as dt
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    ResourceIndexJob,
    ResourceTask,
    Revision,
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
                        Document.__table__,
                        Revision.__table__,
                        DocumentChunk.__table__,
                        ResourceIndexJob.__table__,
                        ResourceTask.__table__,
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

    async with postgres_resource_db() as db:
        resumed = await db.get(ResourceTask, task_id)
        assert resumed.status == 'pending'
        assert resumed.cancel_requested is False
