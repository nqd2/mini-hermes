from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    ResourceIndexJob,
    ResourceTask,
    Revision,
)


@pytest_asyncio.fixture
async def resource_db():
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
                    ResourceIndexJob.__table__,
                    ResourceTask.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_document(sessions):
    document = Document(
        id='document-1',
        owner_id='alice',
        filename='notes.txt',
        mime_type='text/plain',
        current_revision=1,
        source_file_id='upload-1',
    )
    async with sessions() as db:
        db.add(document)
        db.add(
            Revision(
                id='revision-1',
                document_id=document.id,
                number=1,
                object_key='revision-key',
                sha256='revision-hash',
                size=15,
            )
        )
        await db.commit()
    return document


def install_tool_scope(monkeypatch, sessions, document):
    from open_webui.mini_hermes import storage, tools

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def scoped_document(*_args, **_kwargs):
        return document

    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(tools, '_scoped_document', scoped_document)
    monkeypatch.setattr(tools, 'ObjectStore', NoReadStore)
    monkeypatch.setattr(storage, 'ObjectStore', NoReadStore)
    return tools


class NoReadStore:
    async def get(self, _key):
        raise AssertionError('foreground tool must not parse a revision queued for indexing')


@pytest.mark.parametrize(
    ('tool_name', 'arguments'),
    [
        ('read_editable_document', {}),
        ('search_editable_document', {'query': 'budget'}),
    ],
)
@pytest.mark.asyncio
async def test_bounded_read_tools_return_pending_while_worker_indexes(resource_db, monkeypatch, tool_name, arguments):
    document = await seed_document(resource_db)
    tools = install_tool_scope(monkeypatch, resource_db, document)
    tool = getattr(tools, tool_name)

    result = await tool(
        document.id,
        **arguments,
        __user__={'id': 'alice'},
        __resource_scope_ids__=['upload-1'],
    )

    assert result['status'] == 'pending'
    assert result['revision'] == document.current_revision
    async with resource_db() as db:
        job = (await db.execute(select(ResourceIndexJob))).scalar_one()
        chunk_count = (await db.execute(select(func.count()).select_from(DocumentChunk))).scalar_one()
        assert job.status == 'pending'
        assert chunk_count == 0


@pytest.mark.asyncio
async def test_summary_task_queues_indexing_instead_of_parsing_in_the_chat_request(resource_db, monkeypatch):
    document = await seed_document(resource_db)
    tools = install_tool_scope(monkeypatch, resource_db, document)

    result = await tools.start_document_summary_task(
        document.id,
        __user__={'id': 'alice'},
        __resource_scope_ids__=['upload-1'],
    )

    assert result['status'] == 'pending'
    assert result['revision'] == document.current_revision
    async with resource_db() as db:
        job = (await db.execute(select(ResourceIndexJob))).scalar_one()
        task_count = (await db.execute(select(func.count()).select_from(ResourceTask))).scalar_one()
        chunk_count = (await db.execute(select(func.count()).select_from(DocumentChunk))).scalar_one()
        assert job.status == 'pending'
        assert task_count == 0
        assert chunk_count == 0


@pytest.mark.asyncio
async def test_completed_document_task_provides_versioned_coverage_to_the_answer_gate(resource_db, monkeypatch):
    document = await seed_document(resource_db)
    tools = install_tool_scope(monkeypatch, resource_db, document)
    async with resource_db() as db:
        db.add(
            ResourceTask(
                id='summary-task',
                document_id=document.id,
                owner_id='alice',
                chat_id=None,
                revision=document.current_revision,
                kind='summary',
                status='completed',
                phase='complete',
                next_ordinal=1,
                next_group=0,
                level=0,
                total_units=1,
                completed_units=1,
                checkpoint={'evidence_ids': ['chunk-1', 'chunk-2']},
                result='Summary of the document',
                cancel_requested=False,
            )
        )
        await db.commit()

    result = await tools.get_document_task(
        'summary-task', __user__={'id': 'alice'}, __resource_scope_ids__=['upload-1']
    )

    assert result['status'] == 'completed'
    assert result['revision'] == document.current_revision
    assert result['coverage_complete'] is True
    assert result['evidence_ids'] == ['chunk-1', 'chunk-2']


@pytest.mark.asyncio
async def test_completed_document_task_for_old_revision_is_not_current_evidence(resource_db, monkeypatch):
    document = await seed_document(resource_db)
    document.current_revision = 2
    tools = install_tool_scope(monkeypatch, resource_db, document)
    async with resource_db() as db:
        db.add(
            ResourceTask(
                id='stale-summary-task',
                document_id=document.id,
                owner_id='alice',
                chat_id=None,
                revision=1,
                kind='summary',
                status='completed',
                phase='complete',
                next_ordinal=1,
                next_group=0,
                level=0,
                total_units=1,
                completed_units=1,
                checkpoint={'evidence_ids': ['old-chunk']},
                result='Old revision summary',
                cancel_requested=False,
            )
        )
        await db.commit()

    result = await tools.get_document_task(
        'stale-summary-task', __user__={'id': 'alice'}, __resource_scope_ids__=['upload-1']
    )

    assert result['status'] == 'failed'
    assert result['coverage_complete'] is False
    assert result['evidence_ids'] == []


@pytest.mark.asyncio
async def test_csv_chat_upload_adoption_queues_real_resource_indexing(resource_db, monkeypatch, tmp_path):
    from open_webui.mini_hermes.resource_uploads import register_chat_upload
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    source = tmp_path / 'source.csv'
    source.write_bytes(b'name,value\nalpha,1')
    uploaded = type(
        'UploadedFile',
        (),
        {'meta': {'name': 'dataset.csv'}, 'filename': 'source.csv', 'path': 'stored-source'},
    )()

    async def get_uploaded(_file_id, _user_id, db=None):
        return uploaded

    monkeypatch.setattr(Files, 'get_file_by_id_and_user_id', get_uploaded)
    monkeypatch.setattr(Storage, 'get_file', lambda _path: str(source))
    store = type('Store', (), {'put': async_noop, 'get': async_noop})()

    async with resource_db() as db:
        document = await register_chat_upload(db, 'alice', 'upload-csv', store)
        job = (await db.execute(select(ResourceIndexJob))).scalar_one()
        chunk_count = (await db.execute(select(func.count()).select_from(DocumentChunk))).scalar_one()

    assert document.filename == 'dataset.csv'
    assert document.source_file_id == 'upload-csv'
    assert job.status == 'pending'
    assert chunk_count == 0


async def async_noop(*_args, **_kwargs):
    return None
