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


async def _description_worker_database(monkeypatch):
    from open_webui.mini_hermes import worker

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[Folder.__table__, Document.__table__, Revision.__table__],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: object())

    async def load_revision_content(_store, _revision):
        return b'large-document-fixture'

    monkeypatch.setattr(worker, 'get_verified_revision_content', load_revision_content)
    monkeypatch.setattr(worker, 'extract_text', lambda _content, _filename: 'x' * 24_000)
    async with sessions() as db:
        db.add(
            Document(
                id='doc-description',
                owner_id='alice',
                filename='large.txt',
                mime_type='text/plain',
                current_revision=1,
                description_next_attempt_at=now(),
            )
        )
        db.add(
            Revision(
                id='rev-description',
                document_id='doc-description',
                number=1,
                object_key='large',
                sha256='d' * 64,
                size=24_000,
            )
        )
        await db.commit()
    return engine, sessions


@pytest.mark.asyncio
async def test_description_provider_receives_bounded_source_text(monkeypatch):
    from open_webui.mini_hermes import resource_tasks, worker

    engine, sessions = await _description_worker_database(monkeypatch)
    submitted = []

    async def describe(messages, *, max_tokens, temperature=0.1):
        submitted.append(messages[-1]['content'])
        return 'A bounded description.'

    monkeypatch.setattr(resource_tasks, 'call_provider', describe)
    await worker.process_description('doc-description')

    async with sessions() as db:
        document = await db.get(Document, 'doc-description')
        assert document.description_status == 'complete'
        assert document.description == 'A bounded description.'
    assert len(submitted) == 1
    assert len(submitted[0]) <= 8_000
    await engine.dispose()


@pytest.mark.asyncio
async def test_description_context_rejection_is_terminal_until_manual_retry(monkeypatch):
    from fastapi import HTTPException

    from open_webui.mini_hermes import resource_tasks, worker

    engine, sessions = await _description_worker_database(monkeypatch)
    submitted = []

    async def reject_context(messages, *, max_tokens, temperature=0.1):
        submitted.append(messages[-1]['content'])
        raise HTTPException(
            status_code=413,
            detail={
                'code': 'model_context_budget_exceeded',
                'message': 'The supplied request does not fit the configured model context.',
            },
        )

    monkeypatch.setattr(resource_tasks, 'call_provider', reject_context)
    assert await worker.claim_description() == 'doc-description'
    await worker.process_description('doc-description')

    async with sessions() as db:
        document = await db.get(Document, 'doc-description')
        assert document.description_status == 'failed_permanent'
        assert document.description_attempts == 1
    assert len(submitted) == 1
    assert await worker.claim_description() is None
    await engine.dispose()
