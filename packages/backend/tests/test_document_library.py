import pytest
from io import BytesIO
from contextlib import asynccontextmanager
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.access_grants import AccessGrant, AccessGrants
from open_webui.mini_hermes.models import ChatResource, Document, Folder, Revision
from open_webui.mini_hermes.library import document_in_chat_scope, ensure_personal_folder, has_document_access, list_chat_documents, list_personal_documents, move_document, set_chat_document_access
from open_webui.mini_hermes.service import create_document


def valid_docx(text='Office content'):
    from docx import Document as WordDocument

    document = WordDocument()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_personal_folder_is_created_once_and_document_uses_stable_object_key(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as db:
        first = await ensure_personal_folder(db, 'alice')
        second = await ensure_personal_folder(db, 'alice')
        assert first.id == second.id
        assert first.name == 'Personal'

        doc = Document(id='doc-1', owner_id='alice', filename='report.docx',
                       mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                       current_revision=1, folder_id=first.id)
        db.add(doc)
        db.add(Revision(id='rev-1', document_id=doc.id, number=1,
                        object_key=f'users/alice/documents/{doc.id}/revisions/rev-1',
                        sha256='x', size=1))
        await db.commit()
        stored = (await db.execute(select(Revision).where(Revision.document_id == doc.id))).scalar_one()
        assert stored.object_key == 'users/alice/documents/doc-1/revisions/rev-1'

    await engine.dispose()


@pytest.mark.asyncio
async def test_uploaded_document_is_stored_under_owner_prefix():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Store:
        objects = {}

        async def put(self, key, content, mime_type):
            self.objects[key] = content

    store = Store()
    async with sessions() as db:
        content = valid_docx('file-content')
        doc = await create_document(db, store, 'alice', 'report.docx', content)
        revision = (await db.execute(select(Revision).where(Revision.document_id == doc.id))).scalar_one()
        assert doc.folder_id == (await ensure_personal_folder(db, 'alice')).id
        assert revision.object_key.startswith(f'users/alice/documents/{doc.id}/revisions/')
        assert store.objects[revision.object_key] == content
    await engine.dispose()


@pytest.mark.asyncio
async def test_moving_document_changes_metadata_without_moving_object():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        root = await ensure_personal_folder(db, 'alice')
        folder = Folder(id='folder-1', owner_id='alice', parent_id=root.id, name='Reports', is_personal_root=False)
        doc = Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx',
                       current_revision=1, folder_id=root.id)
        revision = Revision(id='rev-1', document_id='doc-1', number=1,
                            object_key='users/alice/documents/doc-1/revisions/rev-1.docx', sha256='x', size=1)
        db.add_all([folder, doc, revision])
        await db.commit()

        moved = await move_document(db, 'doc-1', 'alice', 'folder-1')
        stored_revision = await db.get(Revision, 'rev-1')
        assert moved.folder_id == 'folder-1'
        assert stored_revision.object_key == 'users/alice/documents/doc-1/revisions/rev-1.docx'
    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_read_grant_inherits_but_does_not_grant_write():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[AccessGrant.__table__, Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        folder = Folder(id='folder-1', owner_id='alice', name='Reports', is_personal_root=False)
        document = Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx',
                            current_revision=1, folder_id=folder.id)
        db.add_all([folder, document])
        await db.commit()
        await AccessGrants.grant_access('mh_folder', folder.id, 'user', 'bob', 'read', db=db)
        assert await has_document_access(db, document, 'bob', 'read', user_group_ids=set())
        assert not await has_document_access(db, document, 'bob', 'write', user_group_ids=set())
        assert [item.id for item in await list_personal_documents(db, 'bob')] == ['doc-1']
    await engine.dispose()


@pytest.mark.asyncio
async def test_description_failure_keeps_uploaded_revision_and_is_retryable(monkeypatch):
    from open_webui.mini_hermes import worker
    from open_webui.mini_hermes.models import now

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Store:
        async def get(self, key):
            return b'raw file bytes'

    async with sessions() as db:
        root = await ensure_personal_folder(db, 'alice')
        db.add(Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx',
                        current_revision=1, folder_id=root.id, description_status='processing',
                        description_attempts=1, description_next_attempt_at=now()))
        db.add(Revision(id='rev-1', document_id='doc-1', number=1, object_key='source', sha256='x', size=14))
        await db.commit()

    monkeypatch.setenv('MH_SMALL_TASK_MODEL', '')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: Store())
    await worker.process_description('doc-1')

    async with sessions() as db:
        document = await db.get(Document, 'doc-1')
        revision = await db.get(Revision, 'rev-1')
        assert document.description_status == 'failed'
        assert 'MH_SMALL_TASK_MODEL' in document.description_error
        assert revision.object_key == 'source'
    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_document_access_is_scoped_and_revocable():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        folder = await ensure_personal_folder(db, 'alice')
        db.add(Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/docx',
                        current_revision=1, folder_id=folder.id, source_file_id='upload-1'))
        await db.commit()

        await set_chat_document_access(db, 'chat-1', 'alice', 'doc-1', True)
        assert [item.id for item in await list_chat_documents(db, 'chat-1', 'alice')] == ['doc-1']
        doc = await db.get(Document, 'doc-1')
        assert await document_in_chat_scope(db, doc, 'chat-1', {'upload-1'})
        assert await list_chat_documents(db, 'chat-2', 'alice') == []

        await set_chat_document_access(db, 'chat-1', 'alice', 'doc-1', False)
        assert await list_chat_documents(db, 'chat-1', 'alice') == []
        assert not await document_in_chat_scope(db, doc, 'chat-1', {'upload-1'})
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_list_adopts_chat_attachment_once_and_returns_it(monkeypatch, tmp_path):
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage
    from open_webui.mini_hermes import tools

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source = tmp_path / 'upload.docx'
    source.write_bytes(valid_docx())

    class Store:
        objects = {}

        async def put(self, key, content, mime_type):
            self.objects[key] = content

        async def get(self, key):
            return self.objects[key]

    store = Store()

    async def lookup(file_id, user_id, db=None):
        assert (file_id, user_id) == ('upload-1', 'alice')
        return SimpleNamespace(path='source', filename='upload.docx', meta={'name': 'report.docx'})

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(Files, 'get_file_by_id_and_user_id', lookup)
    monkeypatch.setattr(Storage, 'get_file', lambda path: str(source))
    monkeypatch.setattr(tools, 'ObjectStore', lambda: store)
    monkeypatch.setattr(tools, 'get_async_db', open_db)

    attached = [{'id': 'upload-1', 'type': 'file', 'name': 'report.docx'}]
    listed = await tools.list_editable_documents(
        __user__={'id': 'alice', 'role': 'user'}, __files__=attached, __chat_id__='chat-1'
    )
    listed_again = await tools.list_editable_documents(
        __user__={'id': 'alice', 'role': 'user'}, __files__=attached, __chat_id__='chat-1'
    )
    assert len(listed) == 1
    assert listed[0]['filename'] == 'report.docx'
    assert listed_again == listed

    async with sessions() as db:
        assert len((await db.execute(select(Document))).scalars().all()) == 1
        assert len((await db.execute(select(Revision))).scalars().all()) == 1
        assert len((await db.execute(select(ChatResource))).scalars().all()) == 1
    await engine.dispose()
