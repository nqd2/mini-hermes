from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import ChatResource, Document, DocumentChunk, Folder, ResourceTask, Revision
from open_webui.mini_hermes.resource_tasks import (
    create_summary_task,
    get_summary_task,
    process_summary_task,
    request_task_cancel,
    resume_summary_task,
)


@pytest.mark.asyncio
async def test_summary_task_checkpoints_chunks_and_reduces_with_evidence(monkeypatch):
    from open_webui.mini_hermes import resource_tasks

    async def summarize(text):
        return 'summary:' + text[:30]

    monkeypatch.setattr(resource_tasks, 'summarize_with_provider', summarize)
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
    async with sessions() as db:
        doc = Document(
            id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1
        )
        db.add_all(
            [
                doc,
                Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
                DocumentChunk(
                    id='c1',
                    document_id='doc-1',
                    revision=1,
                    ordinal=0,
                    source_kind='docx',
                    section='document',
                    start_offset=0,
                    end_offset=10,
                    content='first source',
                ),
                DocumentChunk(
                    id='c2',
                    document_id='doc-1',
                    revision=1,
                    ordinal=1,
                    source_kind='docx',
                    section='document',
                    start_offset=10,
                    end_offset=20,
                    content='second source',
                ),
            ]
        )
        await db.commit()
        task = await create_summary_task(db, doc, 'alice', 'chat-1')
        assert (await db.execute(select(DocumentChunk.id))).scalars().all() == ['c1', 'c2']
        for _ in range(8):
            if task.status == 'completed':
                break
            task.status = 'processing'
            await process_summary_task(db, task)
            await db.refresh(task)
        assert task.status == 'completed'
        assert task.result.startswith('summary:')
        status = await get_summary_task(db, task.id, 'alice')
        assert status['progress']['completed_units'] == status['progress']['total_units']
        assert status['evidence_ids'] == ['c1', 'c2']
    await engine.dispose()


@pytest.mark.asyncio
async def test_large_summary_reduces_multiple_groups_across_database_sessions(monkeypatch):
    from open_webui.mini_hermes import resource_tasks

    calls = []

    async def summarize(text):
        calls.append(text)
        return f'summary-{len(calls)}:{text[:30]}'

    monkeypatch.setattr(resource_tasks, 'summarize_with_provider', summarize)
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
    async with sessions() as db:
        doc = Document(
            id='doc-many',
            owner_id='alice',
            filename='large-report.txt',
            mime_type='text/plain',
            current_revision=1,
        )
        db.add_all(
            [
                doc,
                Revision(
                    id='rev-many',
                    document_id=doc.id,
                    number=1,
                    object_key='large-report',
                    sha256='b' * 64,
                    size=130,
                ),
                *[
                    DocumentChunk(
                        id=f'chunk-{ordinal:02}',
                        document_id=doc.id,
                        revision=1,
                        ordinal=ordinal,
                        source_kind='text',
                        section='document',
                        start_offset=ordinal * 10,
                        end_offset=(ordinal + 1) * 10,
                        content=f'content for section {ordinal}',
                    )
                    for ordinal in range(13)
                ],
            ]
        )
        await db.commit()
        task = await create_summary_task(db, doc, 'alice', 'chat-many')
        task_id = task.id

    # Reopen the DB session for every unit to prove that group cursors and evidence survive restarts.
    for _ in range(25):
        async with sessions() as db:
            task = await db.get(ResourceTask, task_id)
            if task.status == 'completed':
                break
            task.status = 'processing'
            await process_summary_task(db, task)

    async with sessions() as db:
        task = await db.get(ResourceTask, task_id)
        status = await get_summary_task(db, task_id, 'alice')
        assert task.status == 'completed'
        assert task.level == 1
        assert status['progress']['completed_units'] == status['progress']['total_units']
        assert status['evidence_ids'] == [f'chunk-{ordinal:02}' for ordinal in range(13)]
        assert len(calls) == 16

    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_and_resume_keeps_checkpoint_and_rejects_changed_revision():
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
    async with sessions() as db:
        doc = Document(
            id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1
        )
        db.add_all(
            [
                doc,
                Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
                DocumentChunk(
                    id='c1',
                    document_id='doc-1',
                    revision=1,
                    ordinal=0,
                    source_kind='docx',
                    section='document',
                    start_offset=0,
                    end_offset=10,
                    content='first source',
                ),
            ]
        )
        await db.commit()
        task = await create_summary_task(db, doc, 'alice')
        task = await request_task_cancel(db, task.id, 'alice')
        assert task.status == 'cancelled'
        task = await resume_summary_task(db, task.id, 'alice', doc)
        assert task.status == 'pending'
        assert task.cancel_requested is False
        assert task.next_ordinal == 0
        assert task.checkpoint == {'items': [], 'reduced': []}
        await request_task_cancel(db, task.id, 'alice')
        doc.current_revision = 2
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await resume_summary_task(db, task.id, 'alice', doc)
        assert error.value.status_code == 409
        assert 'revision changed' in error.value.detail
    await engine.dispose()


@pytest.mark.asyncio
async def test_summary_task_reloads_checkpoint_after_session_restart(monkeypatch):
    from open_webui.mini_hermes import resource_tasks

    async def summarize(text):
        return 'summary:' + text[:30]

    monkeypatch.setattr(resource_tasks, 'summarize_with_provider', summarize)
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

    async with sessions() as db:
        doc = Document(
            id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1
        )
        db.add_all(
            [
                doc,
                Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
                DocumentChunk(
                    id='c1',
                    document_id='doc-1',
                    revision=1,
                    ordinal=0,
                    source_kind='docx',
                    section='document',
                    start_offset=0,
                    end_offset=10,
                    content='first source',
                ),
                DocumentChunk(
                    id='c2',
                    document_id='doc-1',
                    revision=1,
                    ordinal=1,
                    source_kind='docx',
                    section='document',
                    start_offset=10,
                    end_offset=20,
                    content='second source',
                ),
            ]
        )
        await db.commit()
        task = await create_summary_task(db, doc, 'alice', 'chat-1')
        task.status = 'processing'
        await process_summary_task(db, task)
        await db.refresh(task)
        assert task.next_ordinal == 1
        assert task.checkpoint['items'][0]['evidence_ids'] == ['c1']
        task = await request_task_cancel(db, task.id, 'alice')
        task_id = task.id
        assert task.status == 'cancelled'

    async with sessions() as db:
        task = (await db.execute(select(ResourceTask).where(ResourceTask.id == task_id))).scalar_one()
        assert task.next_ordinal == 1
        assert task.checkpoint['items'][0]['evidence_ids'] == ['c1']
        doc = (await db.execute(select(Document).where(Document.id == 'doc-1'))).scalar_one()
        task = await resume_summary_task(db, task.id, 'alice', doc)

        for _ in range(8):
            if task.status == 'completed':
                break
            task.status = 'processing'
            await process_summary_task(db, task)
            await db.refresh(task)

        assert task.status == 'completed'
        status = await get_summary_task(db, task.id, 'alice')
        assert status['evidence_ids'] == ['c1', 'c2']
        assert status['progress']['completed_units'] == status['progress']['total_units']

    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_task_rechecks_chat_resource_access_after_revocation(monkeypatch):
    from open_webui.mini_hermes import tools
    from open_webui.mini_hermes.library import set_chat_document_access

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
                    ChatResource.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(tools, 'get_async_db', open_db)

    async with sessions() as db:
        doc = Document(
            id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1
        )
        db.add_all(
            [
                doc,
                Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
                DocumentChunk(
                    id='c1',
                    document_id='doc-1',
                    revision=1,
                    ordinal=0,
                    source_kind='docx',
                    section='document',
                    start_offset=0,
                    end_offset=10,
                    content='source',
                ),
            ]
        )
        await db.commit()
        await set_chat_document_access(db, 'chat-1', 'alice', 'doc-1', True)
        task = await create_summary_task(db, doc, 'alice', 'chat-1')
        await request_task_cancel(db, task.id, 'alice')
        await set_chat_document_access(db, 'chat-1', 'alice', 'doc-1', False)

    with pytest.raises(HTTPException) as error:
        await tools.resume_document_task(
            task.id,
            __user__={'id': 'alice', 'role': 'user'},
            __files__=[],
            __chat_id__='chat-1',
        )
    assert error.value.status_code == 404
    await engine.dispose()
