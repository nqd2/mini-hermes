import base64
import hashlib
import io
import shutil
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from docx import Document as WordDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import ChatResource, Document, DocumentChunk, Folder, Preview, Revision, SyncJob
from open_webui.mini_hermes.sandbox_service import app, RunRequest, ReadRequest, run_workspace, read_output
from open_webui.mini_hermes.service import commit_preview, create_artifact_preview, publish_generated_document


class MemoryStore:
    def __init__(self):
        self.objects = {}

    async def put(self, key, content, mime_type):
        self.objects[key] = content

    async def get(self, key):
        return self.objects[key]


@pytest.mark.asyncio
async def test_sandbox_runs_code_on_input_and_reads_only_output(tmp_path, monkeypatch):
    monkeypatch.setenv('MH_SANDBOX_WORK_ROOT', str(tmp_path))
    result = await run_workspace(RunRequest(
        workspace_id='a' * 64, language='python',
        code="from pathlib import Path\nPath('outputs/result.pdf').write_bytes(Path('inputs/source.pdf').read_bytes() + b'!')",
        inputs={'inputs/source.pdf': base64.b64encode(b'%PDF').decode()},
        input_revisions={'inputs/source.pdf': 3},
    ))
    assert result['exit_code'] == 0
    assert result['outputs'] == ['outputs/result.pdf']
    read = await read_output(ReadRequest(workspace_id='a' * 64, path='outputs/result.pdf'))
    assert base64.b64decode(read['content_b64']) == b'%PDF!'
    assert read['input_paths'] == ['inputs/source.pdf']
    assert read['input_revisions'] == {'inputs/source.pdf': 3}
    with pytest.raises(HTTPException) as error:
        await read_output(ReadRequest(workspace_id='a' * 64, path='../inputs/source.pdf'))
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_output_keeps_its_input_revision_until_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv('MH_SANDBOX_WORK_ROOT', str(tmp_path))
    workspace_id = 'c' * 64
    inputs = {'inputs/source.pdf': base64.b64encode(b'%PDF').decode()}
    await run_workspace(RunRequest(
        workspace_id=workspace_id, language='python',
        code="from pathlib import Path; Path('outputs/result.pdf').write_bytes(b'first')",
        inputs=inputs, input_revisions={'inputs/source.pdf': 1},
    ))
    await run_workspace(RunRequest(
        workspace_id=workspace_id, language='python', code="print('no edit')",
        inputs=inputs, input_revisions={'inputs/source.pdf': 2},
    ))
    original = await read_output(ReadRequest(workspace_id=workspace_id, path='outputs/result.pdf'))
    assert original['input_revisions'] == {'inputs/source.pdf': 1}
    await run_workspace(RunRequest(
        workspace_id=workspace_id, language='python',
        code="from pathlib import Path; Path('outputs/result.pdf').write_bytes(b'second')",
        inputs=inputs, input_revisions={'inputs/source.pdf': 2},
    ))
    updated = await read_output(ReadRequest(workspace_id=workspace_id, path='outputs/result.pdf'))
    assert updated['input_revisions'] == {'inputs/source.pdf': 2}


def test_sandbox_http_requires_dedicated_token(monkeypatch):
    monkeypatch.setenv('MH_SANDBOX_TOKEN', 'test-only-token')
    with TestClient(app) as client:
        response = client.post('/run', json={'workspace_id': 'a' * 64, 'language': 'python', 'code': 'print(1)'})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_javascript_execution_has_no_service_secrets(tmp_path, monkeypatch):
    if not shutil.which('node'):
        pytest.skip('Node is not installed')
    monkeypatch.setenv('MH_SANDBOX_WORK_ROOT', str(tmp_path))
    monkeypatch.setenv('MH_SANDBOX_TOKEN', 'secret-token')
    result = await run_workspace(RunRequest(
        workspace_id='b' * 64, language='javascript',
        code="require('fs').writeFileSync('outputs/result.pdf', String(process.env.MH_SANDBOX_TOKEN || 'clean'))",
    ))
    assert result['exit_code'] == 0
    output = await read_output(ReadRequest(workspace_id='b' * 64, path='outputs/result.pdf'))
    assert base64.b64decode(output['content_b64']) == b'clean'


@pytest.mark.asyncio
async def test_sandbox_update_requires_preview_approval_and_commit_is_idempotent():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__,
                          Preview.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MemoryStore()
    first_doc = WordDocument()
    first_doc.add_paragraph('first')
    first_bytes = io.BytesIO()
    first_doc.save(first_bytes)
    second_doc = WordDocument()
    second_doc.add_paragraph('second')
    second_bytes = io.BytesIO()
    second_doc.save(second_bytes)
    async with sessions() as db:
        created, revision, job = await publish_generated_document(
            db, store, 'alice', 'outputs/report.docx', first_bytes.getvalue(), 'workspace/output-1')
        assert created.current_revision == 1
        assert revision.number == 1
        assert job is None
        repeated, repeated_revision, _ = await publish_generated_document(
            db, store, 'alice', 'outputs/report.docx', first_bytes.getvalue(), 'workspace/output-1')
        assert repeated.id == created.id
        assert repeated_revision.id == revision.id
        with pytest.raises(HTTPException) as direct_update:
            await publish_generated_document(
                db, store, 'alice', 'outputs/report.docx', second_bytes.getvalue(), 'workspace/output-2',
                source_document_id=created.id, expected_revision=1)
        assert direct_update.value.status_code == 409

        preview = await create_artifact_preview(db, store, created, 'alice', second_bytes.getvalue())
        assert preview.status == 'ready'
        assert preview.base_revision == 1
        assert preview.base_sha256
        assert preview.artifact_sha256 == hashlib.sha256(second_bytes.getvalue()).hexdigest()
        assert created.current_revision == 1
        with pytest.raises(HTTPException) as unapproved:
            await commit_preview(db, store, 'alice', preview.id, sync=False)
        assert unapproved.value.status_code == 400

        approval = {
            'base_revision': preview.base_revision,
            'base_sha256': preview.base_sha256,
            'artifact_sha256': preview.artifact_sha256,
        }
        updated, _ = await commit_preview(db, store, 'alice', preview.id, sync=False, approval=approval)
        again, _ = await commit_preview(db, store, 'alice', preview.id, sync=False, approval=approval)
        assert updated.number == again.number == 2
        assert store.objects[updated.object_key] == second_bytes.getvalue()
        assert len((await db.execute(select(Revision))).scalars().all()) == 2
        assert (await db.execute(select(SyncJob))).scalars().all() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_publish_rejects_invalid_office_bytes_and_pdf_revision():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MemoryStore()
    async with sessions() as db:
        with pytest.raises(HTTPException) as invalid:
            await publish_generated_document(db, store, 'alice', 'outputs/report.docx', b'not-office', 'bad')
        assert invalid.value.status_code == 422
        db.add(Document(id='pdf-1', owner_id='alice', filename='source.pdf', mime_type='application/pdf', current_revision=1))
        await db.commit()
        with pytest.raises(HTTPException) as readonly:
            await publish_generated_document(db, store, 'alice', 'outputs/source.pdf', b'%PDF-1.4', 'pdf-edit', 'pdf-1')
        assert readonly.value.status_code == 400
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_execution_mounts_only_chat_scoped_documents(monkeypatch):
    from open_webui.mini_hermes import tools
    from open_webui.models.chats import Chats

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([
            Document(id='allowed', owner_id='alice', filename='allowed.pdf', mime_type='application/pdf', current_revision=1),
            Document(id='outside', owner_id='alice', filename='outside.pdf', mime_type='application/pdf', current_revision=1),
            Revision(id='rev-allowed', document_id='allowed', number=1, object_key='allowed-key', sha256='x', size=1),
            Revision(id='rev-outside', document_id='outside', number=1, object_key='outside-key', sha256='x', size=1),
            ChatResource(id='scope-1', chat_id='chat-1', document_id='allowed', granted_by='alice'),
        ])
        await db.commit()

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    class Store:
        async def get(self, key):
            return key.encode()

    captured = {}

    class Client:
        async def run(self, payload):
            captured.update(payload)
            return {'exit_code': 0, 'outputs': []}

    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(tools, 'ObjectStore', Store)
    monkeypatch.setattr(tools, 'SandboxClient', Client)
    async def owned_chat(*args, **kwargs):
        return SimpleNamespace(id='chat-1', chat={'messages': []})
    monkeypatch.setattr(Chats, 'get_chat_by_id_and_user_id', owned_chat)
    result = await tools.run_document_code(
        'python', "print('ok')", __user__={'id': 'alice', 'role': 'user'}, __chat_id__='chat-1')
    assert result['exit_code'] == 0
    assert list(captured['inputs']) == ['inputs/allowed/allowed.pdf']
    assert base64.b64decode(captured['inputs']['inputs/allowed/allowed.pdf']) == b'allowed-key'
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_edit_creates_preview_until_user_approval(monkeypatch):
    from open_webui.mini_hermes import tools

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__, ChatResource.__table__, Preview.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source = WordDocument()
    source.add_paragraph('Before')
    buffer = io.BytesIO()
    source.save(buffer)
    store = MemoryStore()
    store.objects['source'] = buffer.getvalue()
    async with sessions() as db:
        db.add_all([
            Document(id='doc-1', owner_id='alice', filename='report.docx', mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document', current_revision=1, drive_file_id='remote-1'),
            Revision(id='rev-1', document_id='doc-1', number=1, object_key='source', sha256=hashlib.sha256(buffer.getvalue()).hexdigest(), size=len(buffer.getvalue())),
            ChatResource(id='scope-1', chat_id='chat-1', document_id='doc-1', granted_by='alice'),
        ])
        await db.commit()

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(tools, 'ObjectStore', lambda: store)
    result = await tools.propose_document_edit(
        'doc-1', [{'op': 'replace_text', 'find': 'Before', 'replace': 'After'}],
        __user__={'id': 'alice', 'role': 'user'}, __chat_id__='chat-1')
    assert result['status'] == 'ready'
    assert result['approval_required'] is True
    assert result['base_revision'] == 1
    assert len(result['base_sha256']) == len(result['artifact_sha256']) == 64
    async with sessions() as db:
        assert (await db.get(Document, 'doc-1')).current_revision == 1
        assert (await db.execute(select(SyncJob))).scalars().all() == []
        from open_webui.mini_hermes.service import commit_preview
        approval = {'base_revision': result['base_revision'], 'base_sha256': result['base_sha256'],
                    'artifact_sha256': result['artifact_sha256']}
        revision, _ = await commit_preview(db, store, 'alice', result['preview_id'], approval=approval)
        assert revision.number == 2
        assert (await db.get(Document, 'doc-1')).current_revision == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_artifacts_lists_generated_outputs_only(monkeypatch):
    from open_webui.mini_hermes import routes

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__, ChatResource.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([
            Document(id='source', owner_id='alice', filename='source.docx', mime_type='application/docx', current_revision=1),
            Document(id='generated', owner_id='alice', filename='result.docx', mime_type='application/docx', current_revision=1, source_file_id='sandbox:abc'),
            Revision(id='rev-source', document_id='source', number=1, object_key='source', sha256='x', size=1),
            Revision(id='rev-generated', document_id='generated', number=1, object_key='result', sha256='y', size=1),
            ChatResource(id='scope-source', chat_id='chat-1', document_id='source', granted_by='alice'),
            ChatResource(id='scope-generated', chat_id='chat-1', document_id='generated', granted_by='alice'),
        ])
        await db.commit()
        async def fake_chat(*args, **kwargs):
            return SimpleNamespace(id='chat-1', chat={'messages': []})
        monkeypatch.setattr(routes.Chats, 'get_chat_by_id_and_user_id', fake_chat)
        result = await routes.list_chat_artifacts('chat-1', user=SimpleNamespace(id='alice', role='user'), db=db)
        assert [item['document_id'] for item in result] == ['generated']
        assert result[0]['revision'] == 1
        assert result[0]['download_url'].endswith('/documents/generated/content?revision=1')
    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_preview_list_exposes_hashes_needed_for_user_approval(monkeypatch):
    from open_webui.mini_hermes import routes

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Preview.__table__,
                          Revision.__table__, ChatResource.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Document(id='approval-doc', owner_id='alice', filename='report.docx',
                        mime_type='application/docx', current_revision=1))
        db.add(ChatResource(id='approval-scope', chat_id='chat-approval',
                            document_id='approval-doc', granted_by='alice'))
        db.add(Preview(
            id='approval-preview', document_id='approval-doc', owner_id='alice',
            base_revision=1, base_sha256='a' * 64, artifact_sha256='b' * 64,
            operations=[{'op': 'replace_text'}], status='ready', preview_key='preview.docx',
        ))
        await db.commit()

        async def fake_chat(*args, **kwargs):
            return SimpleNamespace(id='chat-approval', chat={'messages': []})

        monkeypatch.setattr(routes.Chats, 'get_chat_by_id_and_user_id', fake_chat)
        previews = await routes.list_chat_previews(
            'chat-approval', user=SimpleNamespace(id='alice', role='user'), db=db
        )

        assert previews[0]['base_revision'] == 1
        assert previews[0]['base_sha256'] == 'a' * 64
        assert previews[0]['artifact_sha256'] == 'b' * 64
    await engine.dispose()


@pytest.mark.asyncio
async def test_viewer_history_and_source_resolution_check_read_permission():
    from open_webui.mini_hermes.routes import list_document_revisions, resolve_source_file

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Document(id='viewer-doc', owner_id='alice', filename='report.docx',
                        mime_type='application/docx', source_file_id='upload-1', current_revision=2))
        db.add_all([
            Revision(id='viewer-r1', document_id='viewer-doc', number=1,
                     object_key='one', sha256='1', size=10),
            Revision(id='viewer-r2', document_id='viewer-doc', number=2,
                     object_key='two', sha256='2', size=20),
        ])
        await db.commit()
        alice = SimpleNamespace(id='alice', role='user')
        bob = SimpleNamespace(id='bob', role='user')
        assert (await resolve_source_file('upload-1', user=alice, db=db))['id'] == 'viewer-doc'
        history = await list_document_revisions('viewer-doc', user=alice, db=db)
        assert [item['number'] for item in history] == [2, 1]
        assert history[0]['current'] is True
        for call in (resolve_source_file('upload-1', user=bob, db=db),
                     list_document_revisions('viewer-doc', user=bob, db=db)):
            with pytest.raises(HTTPException) as denied:
                await call
            assert denied.value.status_code in {403, 404}
    await engine.dispose()


@pytest.mark.asyncio
async def test_publish_rejects_output_after_input_is_removed_from_chat(monkeypatch):
    from open_webui.mini_hermes import tools
    from open_webui.models.chats import Chats

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__, ChatResource.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Document(id='old-input', owner_id='alice', filename='source.pdf', mime_type='application/pdf', current_revision=1))
        await db.commit()

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def owned_chat(*args, **kwargs):
        return SimpleNamespace(id='chat-1')

    class Client:
        async def read(self, workspace_id, path):
            return {'content_b64': base64.b64encode(b'%PDF-1.4').decode(),
                    'input_paths': ['inputs/old-input/source.pdf'],
                    'input_revisions': {'inputs/old-input/source.pdf': 1}}

    monkeypatch.setattr(Chats, 'get_chat_by_id_and_user_id', owned_chat)
    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(tools, 'SandboxClient', Client)
    with pytest.raises(HTTPException) as revoked:
        await tools.publish_document_output(
            'outputs/result.pdf', __user__={'id': 'alice', 'role': 'user'}, __chat_id__='chat-1')
    assert revoked.value.status_code == 404
    await engine.dispose()
