import asyncio
import json

import pytest

from weai.agent_runtime import sandbox_service as sandbox
from weai.agent_runtime.workspace_subtasks import execute_workspace_subtasks
from weai.documents.resource_tasks import ProviderRequestError


class LocalSandboxClient:
    async def fs_list(
        self, workspace_id: str, path: str = '.', *, max_depth: int = 3, limit: int = 200
    ) -> dict:
        return await sandbox.fs_list(
            sandbox.FsListRequest(workspace_id=workspace_id, path=path, max_depth=max_depth, limit=limit)
        )

    async def fs_read(
        self,
        workspace_id: str,
        path: str,
        *,
        start_line: int = 1,
        end_line: int = 200,
        max_chars: int = 20_000,
        as_image: bool = False,
        raw_text: bool = False,
    ) -> dict:
        return await sandbox.fs_read(
            sandbox.FsReadRequest(
                workspace_id=workspace_id,
                path=path,
                start_line=start_line,
                end_line=end_line,
                max_chars=max_chars,
                as_image=as_image,
                raw_text=raw_text,
            )
        )

    async def fs_write(self, workspace_id: str, path: str, content: str, *, encoding: str = 'utf-8') -> dict:
        return await sandbox.fs_write(
            sandbox.FsWriteRequest(workspace_id=workspace_id, path=path, content=content, encoding=encoding)
        )


async def _init_workspace(tmp_path, monkeypatch, ws: str) -> LocalSandboxClient:
    monkeypatch.setenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', str(tmp_path))
    await sandbox.sync_workspace(sandbox.SyncRequest(workspace_id=ws, input_revisions={}, inputs={}))
    return LocalSandboxClient()


@pytest.mark.asyncio
async def test_workspace_subtasks_runs_parallel_batches_emits_progress_and_is_idempotent(tmp_path, monkeypatch):
    ws = 'a' * 64
    client = await _init_workspace(tmp_path, monkeypatch, ws)
    await client.fs_write(
        ws, 'work/glossary.json', json.dumps({'SCF': 'tài trợ chuỗi cung ứng'}, ensure_ascii=False)
    )
    for idx in range(1, 5):
        await client.fs_write(ws, f'work/chunks/part_{idx:02d}.json', json.dumps({'id': idx, 'text': f'Chunk {idx}'}))

    events: list[dict] = []
    calls: list[str] = []
    active = 0
    max_active = 0

    async def emitter(event: dict):
        events.append(event)

    async def fake_provider(messages, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        user_msg = messages[-1]['content']
        calls.append(user_msg)
        active -= 1
        assert 'tài trợ chuỗi cung ứng' in messages[0]['content']
        assert kwargs['max_response_chars'] == 60_000
        return f'TRANSLATED: {user_msg.splitlines()[-1]}'

    tasks = [
        {'id': f'p{i}', 'input_path': f'work/chunks/part_{i:02d}.json', 'output_path': f'work/out/part_{i:02d}.txt'}
        for i in range(1, 5)
    ]
    res = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Translate each JSON chunk to Vietnamese.',
        tasks=tasks,
        context_paths=['work/glossary.json'],
        max_concurrency=2,
        event_emitter=emitter,
        provider=fake_provider,
    )

    assert res['status'] == 'completed'
    assert res['completed'] == 4
    assert res['skipped'] == 0
    assert res['failed'] == 0
    assert max_active <= 2
    assert len(calls) == 4
    assert any(e['type'] == 'status' and e['data']['done'] is True for e in events)
    # Summary stays compact and does not leak raw chunk content.
    assert 'TRANSLATED:' not in json.dumps(res)

    out1 = await client.fs_read(ws, 'work/out/part_01.txt', raw_text=True)
    assert out1['content'].startswith('TRANSLATED:')

    # Second run with overwrite=False skips all 4 completed outputs without invoking provider.
    calls.clear()
    res2 = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Translate each JSON chunk to Vietnamese.',
        tasks=tasks,
        overwrite=False,
        provider=fake_provider,
    )
    assert res2['status'] == 'completed'
    assert res2['skipped'] == 4
    assert res2['completed'] == 0
    assert calls == []


@pytest.mark.asyncio
async def test_workspace_subtasks_enforces_95k_and_100k_boundaries_before_provider(tmp_path, monkeypatch):
    ws = 'b' * 64
    client = await _init_workspace(tmp_path, monkeypatch, ws)
    # 46,000 chars exceeds MAX_CHUNK_CHARS (45,000)
    await client.fs_write(ws, 'work/huge.txt', 'A' * 46_000)
    # Tightened preparation limit (e.g., 5,000 chars) rejects before provider call
    await client.fs_write(ws, 'work/medium.txt', 'B' * 6_000)

    invoked = 0

    async def should_not_be_called(messages, **kwargs):
        nonlocal invoked
        invoked += 1
        return 'nope'

    res_huge = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Process text',
        tasks=[{'input_path': 'work/huge.txt', 'output_path': 'work/out_huge.txt'}],
        provider=should_not_be_called,
    )
    assert res_huge['status'] == 'failed'
    assert invoked == 0

    monkeypatch.setenv('AGENT_REQUEST_PREPARATION_MAX_JSON_CHARS', '5000')
    res_budget = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Process text',
        tasks=[{'input_path': 'work/medium.txt', 'output_path': 'work/out_medium.txt'}],
        provider=should_not_be_called,
    )
    assert res_budget['status'] == 'failed'
    assert 'preparation limit' in res_budget['results'][0]['error'].lower()
    assert invoked == 0


@pytest.mark.asyncio
async def test_workspace_subtasks_rejects_unsafe_paths_and_retries_transient_429(tmp_path, monkeypatch):
    ws = 'c' * 64
    client = await _init_workspace(tmp_path, monkeypatch, ws)
    await client.fs_write(ws, 'work/in.txt', 'hello')

    # Rejects writing outside work/
    bad_out = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Echo',
        tasks=[{'input_path': 'work/in.txt', 'output_path': 'inputs/hack.txt'}],
    )
    assert 'error' in bad_out

    bad_trav = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Echo',
        tasks=[{'input_path': 'work/../secret.txt', 'output_path': 'work/out.txt'}],
    )
    assert 'error' in bad_trav

    attempts = 0

    async def flaky_provider(messages, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderRequestError(status_code=429, code='rate_limit', param=None, request_id='r1')
        return 'ok-after-retry'

    # Avoid sleeping in test
    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr('weai.agent_runtime.workspace_subtasks.asyncio.sleep', fast_sleep)

    res = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Echo',
        tasks=[{'input_path': 'work/in.txt', 'output_path': 'work/out.txt'}],
        provider=flaky_provider,
    )
    assert res['status'] == 'completed'
    assert attempts == 2
    saved = await client.fs_read(ws, 'work/out.txt', raw_text=True)
    assert saved['content'] == 'ok-after-retry'


@pytest.mark.asyncio
async def test_workspace_subtasks_input_glob_and_manifest_modes_stay_compact_for_large_batches(tmp_path, monkeypatch):
    ws = 'd' * 64
    client = await _init_workspace(tmp_path, monkeypatch, ws)
    manifest = []
    for idx in range(15):
        in_path = f'work/chunks/chunk_{idx:03d}.json'
        out_path = f'work/translated/chunk_{idx:03d}.json'
        await client.fs_write(ws, in_path, json.dumps({'idx': idx}))
        manifest.append({'id': f'c{idx}', 'input_path': in_path, 'output_path': out_path})
    await client.fs_write(ws, 'work/manifest.json', json.dumps(manifest))

    async def fake_provider(messages, **kwargs):
        assert kwargs['max_tokens'] == 8192
        return '{"ok": true}'

    res = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Translate',
        input_glob='work/chunks/chunk_*.json',
        output_dir='work/translated',
        report_path='work/glob_report.json',
        provider=fake_provider,
    )
    assert res['status'] == 'completed'
    assert res['total'] == 15
    assert res['completed'] == 15
    # Large batches (>10) omit inline `results` to stay well under tool observation budget.
    assert 'results' not in res
    assert res['report_path'] == 'work/glob_report.json'
    assert '...' in res['output_paths_sample']
    assert len(json.dumps(res)) < 1000

    report = json.loads((await client.fs_read(ws, 'work/glob_report.json', raw_text=True))['content'])
    assert len(report['results']) == 15

    # manifest_path mode skips already completed outputs unless overwrite=True
    res_manifest = await execute_workspace_subtasks(
        client=client,
        workspace_id=ws,
        user={'id': 'alice', 'role': 'user'},
        instructions='Translate',
        manifest_path='work/manifest.json',
        provider=fake_provider,
    )
    assert res_manifest['status'] == 'completed'
    assert res_manifest['skipped'] == 15
    assert res_manifest['completed'] == 0
