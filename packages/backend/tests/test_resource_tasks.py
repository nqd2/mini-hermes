import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import Document, DocumentChunk, Folder, ResourceTask, Revision
from open_webui.mini_hermes.resource_tasks import (
    create_summary_task, get_summary_task, process_summary_task,
    request_task_cancel, resume_summary_task,
)


@pytest.mark.asyncio
async def test_summary_task_checkpoints_chunks_and_reduces_with_evidence(monkeypatch):
    from open_webui.mini_hermes import resource_tasks

    async def summarize(text):
        return 'summary:' + text[:30]

    monkeypatch.setattr(resource_tasks, 'summarize_with_provider', summarize)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__,
                          DocumentChunk.__table__, ResourceTask.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        doc = Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1)
        db.add_all([
            doc,
            Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
            DocumentChunk(id='c1', document_id='doc-1', revision=1, ordinal=0, source_kind='docx',
                          section='document', start_offset=0, end_offset=10, content='first source'),
            DocumentChunk(id='c2', document_id='doc-1', revision=1, ordinal=1, source_kind='docx',
                          section='document', start_offset=10, end_offset=20, content='second source'),
        ])
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
async def test_cancel_and_resume_keeps_checkpoint_and_rejects_changed_revision():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__,
                          DocumentChunk.__table__, ResourceTask.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        doc = Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx', current_revision=1)
        db.add_all([
            doc,
            Revision(id='rev-1', document_id='doc-1', number=1, object_key='doc', sha256='a' * 64, size=1),
            DocumentChunk(id='c1', document_id='doc-1', revision=1, ordinal=0, source_kind='docx',
                          section='document', start_offset=0, end_offset=10, content='first source'),
        ])
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
