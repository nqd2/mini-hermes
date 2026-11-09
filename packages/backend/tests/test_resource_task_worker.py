import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    ResourceTask,
    Revision,
    now,
)


@pytest.mark.asyncio
async def test_worker_reclaims_expired_resource_task_lease_after_restart(monkeypatch):
    from open_webui.mini_hermes import worker

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[Folder.__table__, Document.__table__, ResourceTask.__table__],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)

    async with sessions() as db:
        db.add(
            Document(
                id='doc-1',
                owner_id='alice',
                filename='report.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            ResourceTask(
                id='task-1',
                document_id='doc-1',
                owner_id='alice',
                chat_id='chat-1',
                revision=1,
                status='processing',
                lease_until=now() - dt.timedelta(minutes=1),
                checkpoint={'items': [], 'reduced': []},
            )
        )
        await db.commit()

    claimed_id = await worker.claim_resource_task()
    assert claimed_id == 'task-1'
    async with sessions() as db:
        task = (await db.execute(select(ResourceTask).where(ResourceTask.id == 'task-1'))).scalar_one()
        lease_until = task.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=dt.UTC)
        assert task.status == 'processing'
        assert lease_until > now()

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_processes_and_persists_one_summary_checkpoint(monkeypatch):
    from open_webui.mini_hermes import resource_tasks, worker

    async def summarize(text):
        return f'summary:{text}'

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[
                    Folder.__table__,
                    Document.__table__,
                    Revision.__table__,
                    DocumentChunk.__table__,
                    ResourceTask.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(resource_tasks, 'summarize_with_provider', summarize)

    async with sessions() as db:
        db.add_all(
            [
                Document(
                    id='doc-worker',
                    owner_id='alice',
                    filename='worker.txt',
                    mime_type='text/plain',
                    current_revision=1,
                ),
                Revision(
                    id='rev-worker',
                    document_id='doc-worker',
                    number=1,
                    object_key='worker',
                    sha256='c' * 64,
                    size=8,
                ),
                DocumentChunk(
                    id='chunk-worker',
                    document_id='doc-worker',
                    revision=1,
                    ordinal=0,
                    source_kind='text',
                    section='document',
                    start_offset=0,
                    end_offset=8,
                    content='evidence',
                ),
                ResourceTask(
                    id='task-worker',
                    document_id='doc-worker',
                    owner_id='alice',
                    revision=1,
                    status='pending',
                    phase='chunks',
                    next_ordinal=0,
                    next_group=0,
                    level=0,
                    total_units=1,
                    completed_units=0,
                    checkpoint={'items': [], 'reduced': []},
                    cancel_requested=False,
                ),
            ]
        )
        await db.commit()

    assert await worker.claim_resource_task() == 'task-worker'
    await worker.process_resource_task_job('task-worker')

    async with sessions() as db:
        task = (await db.execute(select(ResourceTask).where(ResourceTask.id == 'task-worker'))).scalar_one()
        assert task.status == 'pending'
        assert task.next_ordinal == 1
        assert task.completed_units == 1
        assert task.lease_until is None
        assert task.checkpoint['items'] == [{'summary': 'summary:evidence', 'evidence_ids': ['chunk-worker']}]

    await engine.dispose()
