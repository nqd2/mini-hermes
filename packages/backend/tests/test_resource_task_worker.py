import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import Document, Folder, ResourceTask, now


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
