import asyncio
import os
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.agent_runtime import sandbox_service as sandbox
from weai.agent_runtime.agent_execution import can_answer_task, initialize_task_state, record_tool_result
from weai.utils.chat_runtime.resource_context import resolve_chat_resource_context


def test_project_or_attachment_exposes_workspace_for_natural_request():
    def resolve(text, project=False, files=None):
        return resolve_chat_resource_context(
            messages=[{'role': 'user', 'content': text}], request_metadata={'files': files},
            request_files=[], resource_manifest=[], base_include_file_context=False, in_project=project,
        ).agent_enabled

    assert resolve('Figure out why the test fails', project=True)
    assert resolve('Làm theo hướng dẫn trong đó nhé', files=[{'id': 'upload'}])
    assert not resolve('/stop-tool', project=True)


def test_translated_file_requires_publication():
    state = initialize_task_state('Tôi gửi file docx cần dịch, bạn hỗ trợ dịch sang tiếng Việt và giữ nguyên định dạng nhé.')
    record_tool_result(state, 'view_file', {'text': 'first paragraph', 'evidence_id': 'excerpt'})
    assert not can_answer_task(state)
    record_tool_result(state, 'publish_document_output', {'document_id': 'new-doc', 'revision': 1})
    assert can_answer_task(state)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell fixture')
@pytest.mark.asyncio
async def test_project_terminal_repairs_and_delivers_download(tmp_path, monkeypatch):
    from weai.agent_runtime import tools
    from weai.documents.models import ChatResource, Document
    from weai.internal import db as database
    from weai.models.chats import Chats
    from weai.models.files import File, FileForm, Files
    from weai.models.folders import Folders
    from weai.storage import provider
    from weai.utils.access_control import files as file_access

    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path / 'machines'))
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(provider, 'UPLOAD_DIR', str(tmp_path))
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda db: Document.metadata.create_all(
            db, tables=[Document.__table__, ChatResource.__table__, File.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    folder = SimpleNamespace(id='project', user_id='alice', data={'files': []})
    async def owned_chat(*args, **kwargs):
        return SimpleNamespace(id='chat', folder_id=folder.id, chat={'messages': []})
    async def get_folder(*args, **kwargs):
        return folder
    async def entries(*args, **kwargs):
        return folder.data['files']

    class Client:
        async def run(self, payload):
            return await sandbox.run_workspace(sandbox.RunRequest(**payload))
        async def read(self, workspace_id, path):
            return await sandbox.read_output(sandbox.ReadRequest(workspace_id=workspace_id, path=path))

    monkeypatch.setattr(tools, 'SandboxClient', Client)
    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(Chats, 'get_chat_by_id_and_user_id', owned_chat)
    monkeypatch.setattr(Folders, 'get_folder_by_id', get_folder)
    monkeypatch.setattr(file_access, 'get_owner_accessible_folder_files', entries)
    user = {'id': 'alice', 'role': 'user'}
    context = {'__user__': user, '__chat_id__': 'chat'}
    async with sessions() as db:
        for name, text in {'main.py': 'from config import missing\nprint(answer)', 'config.py': 'answer = 42'}.items():
            src = tmp_path / name
            src.write_text(text)
            await Files.insert_new_file('alice', FileForm(id=name, filename=name, path=str(src)), db=db)
            folder.data['files'].append({'id': name, 'type': 'file'})
    result = await tools.run_workspace_command('cd work && python main.py', **context)
    assert result['exit_code'] and 'ImportError' in result['stderr']
    result = await tools.run_workspace_command(
        "cd work && sed -i 's/import missing/import answer/' main.py && python main.py > result.txt "
        "&& python -m zipfile -c ../outputs/result.zip result.txt",
        **context,
    )
    assert result['exit_code'] == 0, result
    assert result['outputs'] == ['outputs/result.zip']
    receipt = await tools.publish_document_output('outputs/result.zip', **context)
    assert receipt == await tools.publish_document_output('outputs/result.zip', **context)
    assert receipt['download_url'] == f'/api/v1/files/{receipt["file_id"]}/content?attachment=true'
    # This module also defines a route for /content/{file_name}; use the registered /content handler.
    from weai.routers.files import router
    download = next(route.endpoint for route in router.routes if route.path == '/{id}/content')
    async with sessions() as db:
        response = await download(receipt['file_id'], SimpleNamespace(**user), True, db)
        with zipfile.ZipFile(response.path) as archive:
            assert archive.read('result.txt') == b'42\n'
    folder.data['files'] = []
    with pytest.raises(HTTPException) as revoked:
        await tools.publish_document_output('outputs/result.zip', **context)
    assert revoked.value.status_code == 409
    result = await tools.run_workspace_command('ls -A work', **context)
    assert result['exit_code'] == 0 and not result['stdout']
    await engine.dispose()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell fixture')
@pytest.mark.asyncio
async def test_concurrent_commands_and_stderr_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    async def run(code):
        return await sandbox.run_workspace(sandbox.RunRequest(workspace_id='c' * 64, language='shell', code=code))
    first, second = await asyncio.gather(
        run("sleep 0.1 && echo first > work/state"),
        run("cat work/state && python -c 'import sys; sys.stderr.write(\"x\" * 1000000)'"),
    )
    assert first['exit_code'] == second['exit_code'] == 0
    assert second['stdout'].strip() == 'first'
    assert len(second['stderr']) == 20_000


@pytest.mark.asyncio
async def test_large_docx_edit_keeps_original_and_formatting(tmp_path, monkeypatch):
    import base64
    from io import BytesIO
    from docx import Document

    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    doc = Document()
    doc.add_heading('Original', level=1)
    source_text = 'Original term. ' * 100
    for _ in range(140):
        doc.add_paragraph().add_run(source_text).italic = True
    doc.add_table(rows=1, cols=1).cell(0, 0).text = 'Original'
    doc.sections[0].footer.paragraphs[0].text = 'Footer'
    source = BytesIO()
    doc.save(source)
    original = source.getvalue()
    # Exercise a format-preserving script on a fixture larger than a provider request.
    result = await sandbox.run_workspace(sandbox.RunRequest(
        workspace_id='d' * 64, language='python',
        inputs={'inputs/book/thesis.docx': base64.b64encode(original).decode()},
        input_revisions={'inputs/book/thesis.docx': 1},
        code="""from docx import Document
doc = Document('inputs/book/thesis.docx')
for paragraph in [*doc.paragraphs, *doc.tables[0].cell(0, 0).paragraphs]:
    for run in paragraph.runs:
        run.text = run.text.replace('Original', 'Đã sửa')
doc.save('outputs/edited.docx')
""",
    ))
    assert result['exit_code'] == 0, result
    output = await sandbox.read_output(sandbox.ReadRequest(workspace_id='d' * 64, path='outputs/edited.docx'))
    edited = Document(BytesIO(base64.b64decode(output['content_b64'])))
    assert edited.paragraphs[0].style.name == 'Heading 1'
    assert edited.paragraphs[-1].runs[0].italic
    assert edited.paragraphs[-1].text == source_text.replace('Original', 'Đã sửa')
    assert edited.tables[0].cell(0, 0).text == 'Đã sửa'
    assert edited.sections[0].footer.paragraphs[0].text == 'Footer'
    assert (tmp_path / ('d' * 64) / 'inputs/book/thesis.docx').read_bytes() == original


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell fixture')
@pytest.mark.asyncio
async def test_failed_command_cannot_publish_partial_file(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    ws = 'e' * 64
    result = await sandbox.run_workspace(sandbox.RunRequest(
        workspace_id=ws, language='shell', code='echo partial > outputs/file.txt && exit 1'))
    assert result['exit_code'] == 1
    with pytest.raises(HTTPException) as partial:
        await sandbox.read_output(sandbox.ReadRequest(workspace_id=ws, path='outputs/file.txt'))
    assert partial.value.status_code == 409


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell fixture')
@pytest.mark.asyncio
async def test_command_deadline_releases_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    ws = 'f' * 64
    with pytest.raises(HTTPException) as expired:
        await sandbox.run_workspace(sandbox.RunRequest(workspace_id=ws, language='shell',
                                                      code='sleep 10', timeout_seconds=1))
    assert expired.value.status_code == 408
    recovered = await sandbox.run_workspace(sandbox.RunRequest(workspace_id=ws, language='shell', code='echo ready'))
    assert recovered['stdout'].strip() == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize(('prompt', 'expected'), [
    ('Run the project with the attached data', ['data', 'script']),
    ('Inspect input.csv', ['data']),
])
async def test_project_scope_alongside_attachment(prompt, expected):
    from weai.utils.chat_runtime.tool_execution import _invoke_tool
    from weai.utils.tools import get_async_tool_function_and_apply_extra_params

    async def bound_scope(__resource_scope_ids__=None):
        return __resource_scope_ids__

    tool = {'callable': await get_async_tool_function_and_apply_extra_params(bound_scope, {})}
    result = await _invoke_tool('run_workspace_command', {}, tool, False, {}, {
        'resource_history_files': [{'id': 'data', 'type': 'file', 'name': 'input.csv'}],
        'folder_knowledge': [{'id': 'script', 'type': 'file', 'name': 'main.py'}],
        'resource_scope_ids': ['data'], 'resource_execution_requested': True,
        'resource_task_prompt': prompt,
    }, None)
    assert result == expected


@pytest.mark.asyncio
async def test_hash_based_sync_skips_unchanged_inputs_and_resets_on_scope_narrow(tmp_path, monkeypatch):
    import base64

    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    ws = '1' * 64
    rev_a = {'inputs/doc1/a.txt': 1, 'inputs/doc2/b.txt': 1}

    # Initial manifest probe requests both files.
    probe = await sandbox.sync_workspace(sandbox.SyncRequest(workspace_id=ws, input_revisions=rev_a))
    assert probe == {'synced': False, 'missing_paths': ['inputs/doc1/a.txt', 'inputs/doc2/b.txt'], 'input_paths': []}

    # Upload both files.
    uploaded = await sandbox.sync_workspace(
        sandbox.SyncRequest(
            workspace_id=ws,
            input_revisions=rev_a,
            inputs={
                'inputs/doc1/a.txt': base64.b64encode(b'alpha').decode(),
                'inputs/doc2/b.txt': base64.b64encode(b'beta').decode(),
            },
        )
    )
    assert uploaded['synced'] is True
    assert uploaded['missing_paths'] == []

    # Write scratch file in work/ to verify persistence across synced calls.
    await sandbox.fs_write(sandbox.FsWriteRequest(workspace_id=ws, path='work/notes.txt', content='keep-me'))

    # Second probe with identical revisions transfers zero bytes and keeps work/.
    second_probe = await sandbox.sync_workspace(sandbox.SyncRequest(workspace_id=ws, input_revisions=rev_a))
    assert second_probe == {
        'synced': True,
        'missing_paths': [],
        'input_paths': ['inputs/doc1/a.txt', 'inputs/doc2/b.txt'],
    }
    read_note = await sandbox.fs_read(sandbox.FsReadRequest(workspace_id=ws, path='work/notes.txt'))
    assert 'keep-me' in read_note['content']

    # Run Python without resending base64 inputs works seamlessly after sync.
    ran = await sandbox.run_workspace(
        sandbox.RunRequest(
            workspace_id=ws,
            language='python',
            code="print(open('inputs/doc1/a.txt').read(), open('work/notes.txt').read())",
            inputs={},
            input_revisions=rev_a,
        )
    )
    assert ran['exit_code'] == 0
    assert ran['stdout'].strip() == 'alpha keep-me'

    # Narrowing scope (dropping doc2) resets the workspace and drops scratch files.
    narrow_probe = await sandbox.sync_workspace(
        sandbox.SyncRequest(workspace_id=ws, input_revisions={'inputs/doc1/a.txt': 1})
    )
    assert narrow_probe['synced'] is False
    assert narrow_probe['missing_paths'] == ['inputs/doc1/a.txt']
    assert not (tmp_path / ws / 'work' / 'notes.txt').exists()


@pytest.mark.asyncio
async def test_workspace_fs_tools_read_write_edit_search_and_security(tmp_path, monkeypatch):
    import base64

    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    ws = '2' * 64
    revs = {'inputs/doc1/spec.txt': 1}
    await sandbox.sync_workspace(
        sandbox.SyncRequest(
            workspace_id=ws,
            input_revisions=revs,
            inputs={'inputs/doc1/spec.txt': base64.b64encode(b'line1\nhello world\nline3\n').decode()},
        )
    )

    # Write and edit under work/
    written = await sandbox.fs_write(
        sandbox.FsWriteRequest(workspace_id=ws, path='work/app.py', content='x = 1\ny = 2\nprint(x + y)\n')
    )
    assert written['path'] == 'work/app.py'
    assert written['size'] > 0

    edited = await sandbox.fs_edit(
        sandbox.FsEditRequest(workspace_id=ws, path='work/app.py', target='y = 2', replacement='y = 41')
    )
    assert edited['replacements'] == 1

    # Read line range
    sliced = await sandbox.fs_read(sandbox.FsReadRequest(workspace_id=ws, path='work/app.py', start_line=1, end_line=2))
    assert sliced['total_lines'] == 3
    assert '2: y = 41' in sliced['content']
    assert sliced['truncated'] is True

    # List and search
    listed = await sandbox.fs_list(sandbox.FsListRequest(workspace_id=ws, path='.'))
    paths = {entry['path'] for entry in listed['entries']}
    assert 'inputs/doc1/spec.txt' in paths
    assert 'work/app.py' in paths

    found = await sandbox.fs_search(sandbox.FsSearchRequest(workspace_id=ws, pattern='y = 41', path='work'))
    assert len(found['matches']) == 1
    assert found['matches'][0]['path'] == 'work/app.py'
    assert found['matches'][0]['line'] == 2

    # Image inspection mode (1x1 transparent PNG)
    png_bytes = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
    )
    await sandbox.fs_write(
        sandbox.FsWriteRequest(
            workspace_id=ws,
            path='work/page-01.png',
            content=base64.b64encode(png_bytes).decode(),
            encoding='base64',
        )
    )
    img = await sandbox.fs_read(sandbox.FsReadRequest(workspace_id=ws, path='work/page-01.png', as_image=True))
    assert img['mime_type'] == 'image/png'
    assert base64.b64decode(img['content_b64']) == png_bytes

    # Writing directly to outputs/ records valid provenance for /read
    await sandbox.fs_write(sandbox.FsWriteRequest(workspace_id=ws, path='outputs/final.txt', content='done'))
    out = await sandbox.read_output(sandbox.ReadRequest(workspace_id=ws, path='outputs/final.txt'))
    assert base64.b64decode(out['content_b64']) == b'done'
    assert out['input_revisions'] == revs

    # Security boundaries: cannot write/edit inputs/ or traverse with ..
    with pytest.raises(HTTPException) as ro_err:
        await sandbox.fs_write(sandbox.FsWriteRequest(workspace_id=ws, path='inputs/doc1/spec.txt', content='hacked'))
    assert ro_err.value.status_code == 400

    with pytest.raises(HTTPException) as trav_err:
        await sandbox.fs_read(sandbox.FsReadRequest(workspace_id=ws, path='work/../../etc/passwd'))
    assert trav_err.value.status_code == 400


def test_sandbox_resource_defaults_and_translation_intent(monkeypatch):
    from weai.agent_runtime.agent_execution import infer_required_action
    from weai.agent_runtime.execution.contracts import ExecutionBudget

    monkeypatch.delenv('AGENT_RUNTIME_SANDBOX_MAX_OPEN_FILES', raising=False)
    monkeypatch.delenv('AGENT_RUNTIME_SANDBOX_WORKSPACE_TTL_SECONDS', raising=False)
    monkeypatch.delenv('AGENT_RUNTIME_MAX_MODEL_CALLS', raising=False)
    monkeypatch.delenv('AGENT_RUNTIME_MAX_READS', raising=False)

    assert sandbox._max_open_files() >= 512
    assert sandbox._workspace_ttl_seconds() == 86_400
    budget = ExecutionBudget()
    assert budget.max_model_calls == 40
    assert budget.max_reads == 80

    assert infer_required_action('Dịch file docx này sang tiếng Việt') == 'create'
    assert infer_required_action('Translate paragraph 2 of this docx to Vietnamese') == 'read'
