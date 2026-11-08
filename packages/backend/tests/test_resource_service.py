from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes import resource_service
from open_webui.mini_hermes.chunks import replace_document_chunks
from open_webui.mini_hermes.execution.contracts import ResourceIndexPending
from open_webui.mini_hermes.models import ChatResource, Document, DocumentChunk, Folder, ResourceIndexJob, Revision
from open_webui.mini_hermes.resource_indexing import process_resource_index_job


@pytest.mark.asyncio
async def test_full_context_requires_and_returns_every_indexed_chunk(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__,
                          DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    content = ('Computer Science degree\n' * 250).encode()
    document = Document(
        id='doc-1', owner_id='alice', filename='programs.txt', mime_type='text/plain',
        current_revision=1, source_file_id='upload-1',
    )

    class Store:
        async def get(self, key):
            assert key == 'revision-1'
            return content

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def register(db, user_id, file_id, store):
        assert (file_id, user_id) == ('upload-1', 'alice')
        return document

    monkeypatch.setattr(resource_service, 'get_async_db', open_db)
    monkeypatch.setattr(resource_service, 'ObjectStore', Store)
    monkeypatch.setattr(resource_service, 'register_chat_upload', register)
    async with sessions() as db:
        db.add(document)
        db.add(Revision(
            id='revision-1', document_id='doc-1', number=1, object_key='revision-1',
            sha256='unused', size=len(content),
        ))
        await replace_document_chunks(db, 'doc-1', 1, 'programs.txt', content)
        await db.commit()

    full = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-1', 'type': 'file'}], None, 'summarize all',
        full_context=True, max_total_chars=10_000,
    )
    assert len(full) > 1
    assert all(item['metadata'][0]['coverage_complete'] for item in full)
    assert len({item['metadata'][0]['evidence_id'] for item in full}) == len(full)

    partial = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-1', 'type': 'file'}], None, 'Computer Science',
        full_context=True, max_total_chars=10,
    )
    assert partial
    assert all(item['metadata'][0]['coverage_complete'] is False for item in partial)
    await engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_prioritizes_current_attachment_before_older_chat_files(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__,
                          DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    docs = {
        'upload-old': Document(
            id='doc-old', owner_id='alice', filename='A-old.txt', mime_type='text/plain',
            current_revision=1, source_file_id='upload-old',
        ),
        'upload-current': Document(
            id='doc-current', owner_id='alice', filename='Z-current.txt', mime_type='text/plain',
            current_revision=1, source_file_id='upload-current',
        ),
    }

    class Store:
        async def get(self, key):
            return ('content for ' + key).encode()

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def register(db, user_id, file_id, store):
        return docs.get(file_id)

    monkeypatch.setattr(resource_service, 'get_async_db', open_db)
    monkeypatch.setattr(resource_service, 'ObjectStore', Store)
    monkeypatch.setattr(resource_service, 'register_chat_upload', register)
    async with sessions() as db:
        db.add_all(list(docs.values()))
        db.add_all([
            Revision(id='rev-old', document_id='doc-old', number=1, object_key='old', sha256='x', size=1),
            Revision(id='rev-current', document_id='doc-current', number=1, object_key='current', sha256='x', size=1),
        ])
        await replace_document_chunks(db, 'doc-old', 1, 'A-old.txt', b'content for old')
        await replace_document_chunks(db, 'doc-current', 1, 'Z-current.txt', b'content for current')
        await db.commit()

    results = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-old', 'type': 'file'}, {'id': 'upload-current', 'type': 'file'}],
        None, 'content for', max_chunks=1, per_document=1,
        priority_file_ids=['upload-current'],
    )
    assert results[0]['source']['id'] == 'upload-current'

    scoped = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-old', 'type': 'file'}, {'id': 'upload-current', 'type': 'file'}],
        None, 'content', scope_file_ids=['upload-old'],
    )
    assert scoped
    assert {source['source']['id'] for source in scoped} == {'upload-old'}
    await engine.dispose()


@pytest.mark.asyncio
async def test_retrieval_does_not_treat_first_chunk_as_a_match(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__,
                          DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    document = Document(
        id='doc-1', owner_id='alice', filename='programs.txt', mime_type='text/plain',
        current_revision=1, source_file_id='upload-1',
    )

    class Store:
        async def get(self, key):
            return b'first chunk has no match\nsecond chunk also unrelated'

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def register(db, user_id, file_id, store):
        return document

    monkeypatch.setattr(resource_service, 'get_async_db', open_db)
    monkeypatch.setattr(resource_service, 'ObjectStore', Store)
    monkeypatch.setattr(resource_service, 'register_chat_upload', register)
    async with sessions() as db:
        db.add(document)
        db.add(Revision(
            id='revision-1', document_id='doc-1', number=1, object_key='revision-1',
            sha256='unused', size=1,
        ))
        await db.commit()

    with pytest.raises(ResourceIndexPending):
        await resource_service.search_chat_files(
            'alice', [{'id': 'upload-1', 'type': 'file'}], None, 'missing concept',
        )
    async with sessions() as db:
        index_job = (await db.execute(select(ResourceIndexJob))).scalar_one()
        assert await process_resource_index_job(db, index_job.id, Store())

    results = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-1', 'type': 'file'}], None, 'missing concept',
    )

    assert results == []
    fallback = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-1', 'type': 'file'}], None, 'missing concept',
        bounded_initial_read=True,
    )
    assert fallback
    assert fallback[0]['metadata'][0]['retrieval_mode'] == 'bounded_initial_read'
    assert fallback[0]['metadata'][0]['coverage_complete'] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_resource_manifest_exposes_metadata_without_document_content(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__,
                          DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(resource_service, 'get_async_db', open_db)
    async with sessions() as db:
        db.add(Document(
            id='doc-1', owner_id='alice', filename='M.xlsx', mime_type='application/xlsx',
            current_revision=2, source_file_id='upload-1',
        ))
        db.add(ChatResource(
            id='chat-resource-1', chat_id='chat-1', document_id='doc-1', granted_by='alice',
        ))
        await db.commit()

    manifest = await resource_service.list_chat_resource_manifest('chat-1', 'alice')

    assert manifest == [{
        'id': 'upload-1', 'resource_id': 'doc-1', 'type': 'file',
        'name': 'M.xlsx', 'filename': 'M.xlsx', 'revision': 2,
    }]
    await engine.dispose()


@pytest.mark.asyncio
async def test_saved_chat_retrieval_keeps_initial_read_inside_explicit_scope(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__,
                          DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    docs = {
        'upload-1': Document(
            id='doc-1', owner_id='alice', filename='a.txt', mime_type='text/plain',
            current_revision=1, source_file_id='upload-1',
        ),
        'upload-2': Document(
            id='doc-2', owner_id='alice', filename='b.txt', mime_type='text/plain',
            current_revision=1, source_file_id='upload-2',
        ),
    }

    class Store:
        async def get(self, key):
            return b'ordinary information' if key == 'rev-1' else b'tuition amount is 500'

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def register(db, user_id, file_id, store):
        return docs.get(file_id)

    async def no_op_access(*args, **kwargs):
        return None

    async def chat_attachments(*args, **kwargs):
        return ['upload-1', 'upload-2']

    monkeypatch.setattr(resource_service, 'get_async_db', open_db)
    monkeypatch.setattr(resource_service, 'ObjectStore', Store)
    monkeypatch.setattr(resource_service, 'register_chat_upload', register)
    monkeypatch.setattr(resource_service, 'chat_attachment_file_ids', chat_attachments)
    monkeypatch.setattr(resource_service, 'set_chat_document_access', no_op_access)
    async with sessions() as db:
        db.add_all(list(docs.values()))
        db.add_all([
            Revision(id='rev-1', document_id='doc-1', number=1, object_key='rev-1', sha256='a', size=1),
            Revision(id='rev-2', document_id='doc-2', number=1, object_key='rev-2', sha256='b', size=1),
            ChatResource(id='cr-1', chat_id='chat-1', document_id='doc-1', granted_by='alice'),
            ChatResource(id='cr-2', chat_id='chat-1', document_id='doc-2', granted_by='alice'),
        ])
        await replace_document_chunks(db, 'doc-1', 1, 'a.txt', b'ordinary information')
        await replace_document_chunks(db, 'doc-2', 1, 'b.txt', b'tuition amount is 500')
        await db.commit()

    results = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-1', 'type': 'file'}], 'chat-1', 'tuition',
        scope_file_ids=['upload-1'],
    )

    assert results == []
    await engine.dispose()
