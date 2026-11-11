from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime.models import (
    AgentArtifact,
    AgentCheckpoint,
    AgentEvent,
    AgentFileContent,
    AgentSession,
    AgentToolInvocation,
    AgentTurn,
    Base,
)
from open_webui.agent_runtime.repository import AgentRepository
from open_webui.agent_runtime.runtime import AgentRuntime, _is_retry_safe_builtin
from open_webui.agent_runtime.artifacts import AgentArtifactStore
from open_webui.agent_runtime.outputs import ToolOutputManager
from open_webui.mini_hermes.context_budget import estimate_tokens
from open_webui.utils.chat_runtime import tool_execution
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_canonical_runner_persists_turn_and_resumes_approval_on_same_turn(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    metadata = {}
    started = []

    async def pause():
        started.append('pause')
        return {'status': True, 'paused': True}

    first = await runtime.run_chat_turn(
        owner_id='owner', chat_id='channel:1', chat_message_id='message-1', user_message_id='user-1',
        model_id='model-a', request_payload={'messages': [{'role': 'user', 'content': 'hello'}]},
        metadata=metadata, handler=pause,
    )
    turn_id = metadata['agent_turn_id']
    assert first['paused'] is True
    assert (await repository.get_turn(turn_id)).status == 'waiting_approval'

    resumed_metadata = {}

    async def finish():
        started.append('finish')
        return {'status': True}

    await runtime.resume(turn_id, owner_id='owner', metadata=resumed_metadata, handler=finish)

    assert resumed_metadata['agent_turn_id'] == turn_id
    assert (await repository.get_turn(turn_id)).status == 'completed'
    assert started == ['pause', 'finish']
    events = await repository.list_events(turn_id)
    assert [item.event_type for item in events] == [
        'turn.queued', 'turn.started', 'turn.waiting_approval', 'turn.started', 'turn.completed'
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_operation_interrupts_handler_and_records_terminal_state(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-cancel')
    metadata = {}
    handler_started = asyncio.Event()

    async def wait_forever():
        handler_started.set()
        await asyncio.Future()

    running = asyncio.create_task(runtime.start(
        owner_id='owner', chat_id='channel:cancel', chat_message_id='message-cancel', user_message_id='user-cancel',
        model_id='model-a', request_payload={'messages': []}, metadata=metadata, handler=wait_forever,
    ))
    try:
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        result = await runtime.cancel(metadata['agent_turn_id'], owner_id='owner')
        with pytest.raises(asyncio.CancelledError):
            await running

        turn = await repository.get_turn(metadata['agent_turn_id'])
        events = await repository.list_events(turn.id)
        assert result['cancel_requested'] is True
        assert turn.status == 'cancelled'
        assert events[-1].event_type == 'turn.cancelled'
    finally:
        if not running.done():
            running.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_start_retry_after_completion_returns_same_run_without_reexecution(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-idempotent')
    metadata = {}
    calls = []

    async def handler():
        calls.append('executed')
        return {'answer': 'done'}

    request = {
        'owner_id': 'owner',
        'chat_id': 'channel:idempotent-complete',
        'chat_message_id': 'assistant-idempotent-complete',
        'user_message_id': 'user-idempotent-complete',
        'model_id': 'model-a',
        'request_payload': {'messages': [{'role': 'user', 'content': 'hello'}]},
        'metadata': metadata,
        'handler': handler,
    }
    try:
        first = await runtime.start(**request)
        turn_id = metadata['agent_turn_id']
        duplicate_metadata = {}
        duplicate = await runtime.start(**{**request, 'metadata': duplicate_metadata})

        assert first == {'answer': 'done'}
        assert duplicate['turn_id'] == turn_id
        assert duplicate['status'] == 'completed'
        assert duplicate['idempotent_replay'] is True
        assert duplicate_metadata['agent_turn_id'] == turn_id
        assert calls == ['executed']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_start_retry_returns_running_run_without_claiming_twice(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-concurrent-idempotent')
    metadata = {}
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    calls = []

    async def handler():
        calls.append('executed')
        handler_started.set()
        await release_handler.wait()
        return {'answer': 'done'}

    request = {
        'owner_id': 'owner',
        'chat_id': 'channel:idempotent-running',
        'chat_message_id': 'assistant-idempotent-running',
        'user_message_id': 'user-idempotent-running',
        'model_id': 'model-a',
        'request_payload': {'messages': [{'role': 'user', 'content': 'hello'}]},
        'handler': handler,
    }
    first_task = asyncio.create_task(runtime.start(**{**request, 'metadata': metadata}))
    try:
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        duplicate = await runtime.start(**{**request, 'metadata': {}})
        assert duplicate['turn_id'] == metadata['agent_turn_id']
        assert duplicate['status'] == 'running'
        assert duplicate['idempotent_replay'] is True
        assert calls == ['executed']
    finally:
        release_handler.set()
        await first_task
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_start_does_not_resume_waiting_approval_turn(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-approval-idempotent')
    metadata = {}
    calls = []

    async def pause_for_approval():
        calls.append('initial')
        return {'paused': True}

    request = {
        'owner_id': 'owner',
        'chat_id': 'channel:idempotent-approval',
        'chat_message_id': 'assistant-idempotent-approval',
        'user_message_id': 'user-idempotent-approval',
        'model_id': 'model-a',
        'request_payload': {'messages': [{'role': 'user', 'content': 'hello'}]},
        'handler': pause_for_approval,
    }
    try:
        first = await runtime.start(**{**request, 'metadata': metadata})

        async def unauthorized_resume():
            calls.append('unapproved-resume')
            return {'answer': 'done'}

        duplicate = await runtime.start(**{**request, 'handler': unauthorized_resume, 'metadata': {}})
        assert first['paused'] is True
        assert duplicate['turn_id'] == metadata['agent_turn_id']
        assert duplicate['status'] == 'waiting_approval'
        assert duplicate['idempotent_replay'] is True
        assert calls == ['initial']

        await runtime.resume(
            metadata['agent_turn_id'],
            owner_id='owner',
            metadata={},
            handler=unauthorized_resume,
        )
        assert calls == ['initial', 'unapproved-resume']
        assert (await repository.get_turn(metadata['agent_turn_id'])).status == 'completed'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stream_gap_recovery_includes_owner_scoped_status_snapshot(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-stream')
    try:
        session = await repository.create_session(owner_id='owner', chat_id='channel:stream')
        turn = await repository.create_turn(session.id, {'messages': []})
        await repository.append_event(turn.id, 'assistant.delta', {'text': 'one'})
        await repository.append_event(turn.id, 'assistant.delta', {'text': 'two'})
        async with repository._session_factory() as db:
            from open_webui.agent_runtime.models import AgentEvent

            await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == turn.id, AgentEvent.sequence == 2))
            await db.commit()

        page = await runtime.stream(turn.id, owner_id='owner', after_sequence=1)
        assert page['gap_detected'] is True
        assert page['snapshot_required'] is True
        assert page['snapshot']['turn_id'] == turn.id
        assert page['snapshot']['run_binding']['engine_run_id'] == turn.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_turn_is_durable_and_claimable_after_restart(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-a')
    metadata = {}

    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await runtime.run_chat_turn(
            owner_id='owner', chat_id='channel:2', chat_message_id='message-2', user_message_id='user-2',
            model_id='model-a', request_payload={'messages': []}, metadata=metadata, handler=cancel,
        )

    turn = await repository.get_turn(metadata['agent_turn_id'])
    assert turn.status == 'interrupted'
    await engine.dispose()

    engine, repository = await _repository(tmp_path)
    resumed_runtime = AgentRuntime(repository, worker_id='worker-b')
    resumed_metadata = {}

    async def finish_after_restart():
        return {'status': True}

    await resumed_runtime.run_chat_turn(
        owner_id='owner', chat_id='channel:2', chat_message_id='message-2', user_message_id='user-2',
        model_id='model-a', request_payload={'messages': []}, metadata=resumed_metadata,
        handler=finish_after_restart,
    )

    resumed = await repository.get_turn(turn.id)
    assert resumed_metadata['agent_turn_id'] == turn.id
    assert resumed.status == 'completed'
    assert resumed.attempts == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_run_does_not_import_existing_chat_messages(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository)
    metadata = {}

    async def finish():
        return {'status': True}

    await runtime.run_chat_turn(
        owner_id='owner',
        chat_id='channel:new',
        chat_message_id='assistant-current',
        user_message_id='user-current',
        model_id='model-a',
        request_payload={'messages': [{'role': 'user', 'content': 'current request'}]},
        metadata=metadata,
        handler=finish,
    )

    async with repository._session_factory() as db:
        turns = list((await db.scalars(select(AgentTurn))).all())
    assert len(turns) == 1
    assert turns[0].user_message_id == 'user-current'
    assert metadata['agent_turn_id'] == turns[0].id
    await engine.dispose()


@pytest.mark.asyncio
async def test_large_tool_result_is_artifact_backed_and_never_reexecuted(tmp_path):
    engine, repository = await _repository(tmp_path)
    objects = _ObjectStore()
    artifacts = AgentArtifactStore(repository, objects)
    runtime = AgentRuntime(
        repository,
        artifact_store=artifacts,
        output_manager=ToolOutputManager(max_tokens=64),
        worker_id='worker',
    )
    session = await repository.create_session('owner', 'channel:tools')
    turn = await repository.create_turn(session.id, {'messages': []}, model_id='small-test')
    await repository.claim_turn_by_id(turn.id, 'worker')
    _, acquired = await runtime.begin_tool_call(
        turn.id,
        call_id='read-1',
        tool_name='read_file',
        arguments={'path': 'large.txt'},
        tool={'type': 'builtin'},
    )

    assert acquired is True
    full_result = 'S' + ('x' * 999_998) + 'E'
    preview = await runtime.complete_tool_call(turn.id, 'read-1', full_result, owner_id='owner')
    invocation = await repository.get_tool_call(turn.id, 'read-1')
    artifact = await artifacts.repository.get_artifact(invocation.result_artifact_id, owner_id='owner')
    cached, acquired_again = await runtime.begin_tool_call(
        turn.id,
        call_id='read-1',
        tool_name='read_file',
        arguments={'path': 'large.txt'},
        tool={'type': 'builtin'},
    )

    assert '[output truncated;' in preview
    assert f'artifact_id={artifact.id}' in preview
    assert f'chars={len(full_result)}' in preview
    assert len(full_result) == 1_000_000
    assert artifact is not None
    assert objects.values[artifact.object_key].decode() == full_result
    assert acquired_again is False
    assert cached.result_data == preview
    await engine.dispose()


@pytest.mark.asyncio
async def test_tool_result_uses_remaining_context_and_json_budget(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(
        repository,
        output_manager=ToolOutputManager(max_tokens=1000, max_chars=8000),
        worker_id='worker',
    )
    session = await repository.create_session('owner', 'channel:budgeted-tool')
    turn = await repository.create_turn(session.id, {'messages': []}, model_id='small-test')
    await repository.claim_turn_by_id(turn.id, 'worker')
    await runtime.begin_tool_call(
        turn.id,
        call_id='read-1',
        tool_name='read_file',
        arguments={'path': 'large.txt'},
        tool={'type': 'builtin'},
    )
    await repository.save_checkpoint(
        turn.id,
        {
            'metrics': {
                'serialized_chars': 93_000,
                'estimated_input_tokens': 760,
                'context_window_tokens': 1_000,
                'reserved_output_tokens': 200,
            }
        },
        kind='context',
    )

    result = 'HEAD ' + ('alpha beta gamma delta ' * 300) + ' TAIL'
    preview = await runtime.complete_tool_call(turn.id, 'read-1', result, owner_id='owner')

    assert estimate_tokens(preview, 'small-test') <= 40
    assert len(preview) <= 1_488
    assert preview.startswith('HEAD ')
    assert preview.endswith(' TAIL')
    await engine.dispose()


@pytest.mark.asyncio
async def test_client_event_is_committed_before_emit_and_has_replay_cursor(tmp_path):
    engine, repository = await _repository(tmp_path)
    session = await repository.create_session('owner', 'channel:events')
    turn = await repository.create_turn(session.id, {'messages': []}, model_id='small-test')
    await repository.claim_turn_by_id(turn.id, 'worker')
    runtime = AgentRuntime(repository, worker_id='worker')
    delivered = []

    async def emitter(event):
        persisted = await repository.list_events(turn.id)
        assert [item for item in persisted if item.event_type.startswith('client.')]
        delivered.append(event)

    wrapped = runtime.wrap_event_emitter(turn.id, emitter, message_id='assistant-1')
    await wrapped({'type': 'chat:completion', 'data': {'type': 'content.delta', 'delta': 'token'}})
    await wrapped({'type': 'chat:completion', 'data': {'done': True, 'content': 'final'}})

    events = await repository.list_events(turn.id)
    client_events = [event for event in events if event.event_type.startswith('client.')]
    assert len(client_events) == 2
    assert all(event.published_at is not None for event in client_events)
    assert delivered[0]['data']['runtime_turn_id'] == turn.id
    assert delivered[0]['data']['runtime_event_id'] == client_events[0].id
    assert delivered[0]['data']['runtime_event_sequence'] == client_events[0].sequence
    assert delivered[1]['data']['runtime_turn_id'] == turn.id
    assert delivered[1]['data']['runtime_event_id'] == client_events[1].id
    assert delivered[1]['data']['runtime_event_sequence'] == client_events[1].sequence
    replay = await repository.list_events(turn.id, after_sequence=client_events[0].sequence)
    assert len(replay) == 1
    assert replay[0].id == client_events[1].id
    assert replay[0].payload['event']['data']['content'] == 'final'
    await engine.dispose()


def test_tool_retry_safety_uses_an_explicit_read_only_registry():
    assert _is_retry_safe_builtin('read_agent_artifact', {'type': 'builtin'}) is True
    assert _is_retry_safe_builtin('get_current_timestamp', {'type': 'builtin'}) is True
    assert _is_retry_safe_builtin('get_external_contact', {'type': 'builtin'}) is False
    assert _is_retry_safe_builtin('read_agent_artifact', {'type': 'function'}) is False
    assert _is_retry_safe_builtin('read_agent_artifact', {'type': 'builtin', 'direct': True}) is False


@pytest.mark.asyncio
async def test_failed_side_effect_tool_is_quarantined_as_unknown(tmp_path, monkeypatch):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    session = await repository.create_session('owner', 'channel:unknown-tool')
    turn = await repository.create_turn(session.id, {'messages': []}, model_id='test')
    await repository.claim_turn_by_id(turn.id, 'worker')
    monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))

    async def failed_tool(*args, **kwargs):
        raise RuntimeError('remote service disconnected after request')

    monkeypatch.setattr(tool_execution, '_invoke_tool', failed_tool)
    tool = {
        'type': 'builtin',
        'spec': {'parameters': {'properties': {'to': {}, 'text': {}}}},
    }
    metadata = {
        'agent_turn_id': turn.id,
        'user_id': 'owner',
        'chat_id': 'channel:unknown-tool',
        'params': {'tool_approval_mode': 'full'},
        'tools': {'send_message': tool},
    }

    with pytest.raises(HTTPException) as error:
        await tool_execution.execute_tool_call(
            {},
            metadata,
            None,
            {'id': 'call-1', 'call_id': 'call-1', 'name': 'send_message', 'arguments': '{}'},
        )

    invocation = await repository.get_tool_call(turn.id, 'call-1')
    current_turn = await repository.get_turn(turn.id)
    assert error.value.status_code == 502
    assert invocation.status == 'outcome_unknown'
    assert current_turn.status == 'paused'
    await engine.dispose()


async def _repository(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "runner.db"}')
    async with engine.begin() as connection:
        runtime_tables = [
            AgentSession.__table__,
            AgentTurn.__table__,
            AgentEvent.__table__,
            AgentCheckpoint.__table__,
            AgentToolInvocation.__table__,
            AgentArtifact.__table__,
            AgentFileContent.__table__,
        ]
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(sync_connection, tables=runtime_tables)
        )
    return engine, AgentRepository(async_sessionmaker(engine, expire_on_commit=False))


class _ObjectStore:
    def __init__(self):
        self.values = {}

    async def put(self, key, content, mime_type):
        self.values[key] = content

    async def get_range(self, key, offset, length):
        return self.values[key][offset:offset + length]

    async def delete(self, key):
        self.values.pop(key, None)
