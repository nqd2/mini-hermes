from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import ChatResource, Document, DocumentChunk, Folder, Revision
from open_webui.mini_hermes import resource_service


@pytest.mark.asyncio
async def test_full_context_requires_and_returns_every_indexed_chunk(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__,
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
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__,
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
        await db.commit()

    results = await resource_service.search_chat_files(
        'alice', [{'id': 'upload-old', 'type': 'file'}, {'id': 'upload-current', 'type': 'file'}],
        None, 'no matching text', max_chunks=1, per_document=1,
        priority_file_ids=['upload-current'],
    )
    assert results[0]['source']['id'] == 'upload-current'
    await engine.dispose()
