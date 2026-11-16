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
async def test_continuation_is_a_new_idempotent_run_on_the_same_message(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    executions = []

    async def handler():
        executions.append(True)
        session = await repository.get_session_for_chat('channel:continue', owner_id='owner')
        async with repository._session_factory() as db:
            turn = await db.scalar(select(AgentTurn).where(AgentTurn.session_id == session.id)
                                   .order_by(AgentTurn.sequence.desc()).limit(1))
        await repository.bind_run_contract(turn.id, tool_contract_version='tools-v1',
                                           skill_snapshot_version='skills-v1', run_attempt=1)
        return {'answer': 'done'}

    request = dict(owner_id='owner', chat_id='channel:continue', chat_message_id='assistant',
                   user_message_id='user', model_id='model', handler=handler)
    first_metadata = {}
    try:
        await runtime.start(**request, request_payload={'messages': [{'role': 'user', 'content': 'analyse'}]},
                            metadata=first_metadata)
        continuation = {'assistant_message_id': 'assistant',
                        'continuation_id': '224e5d66-f3b5-48ac-9926-6d664a39f7db'}
        payload = {'messages': [{'role': 'user', 'content': 'analyse'},
                                {'role': 'assistant', 'content': 'partial answer'}],
                   'metadata': {'task_id': 'first-dispatch', 'assistant_message_id': 'assistant'}}
        await runtime.start(**request, request_payload=payload, metadata=continuation)
        replay = await runtime.start(**request, request_payload={**payload, 'metadata': {
                                         **payload['metadata'], 'task_id': 'retry-dispatch'}},
                                     metadata={'assistant_message_id': 'assistant',
                                               'continuation_id': continuation['continuation_id']})
        assert continuation['agent_turn_id'] != first_metadata['agent_turn_id']
        assert replay['turn_id'] == continuation['agent_turn_id']
        assert replay['idempotent_replay'] is True
        assert len(executions) == 2
        with pytest.raises(HTTPException) as error:
            await runtime.start(**request, request_payload={'messages': []},
                                metadata={'assistant_message_id': 'assistant',
                                          'continuation_id': continuation['continuation_id']})
        assert error.value.detail['code'] == 'idempotency_key_reused'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_continuation_cannot_bypass_approval_or_unknown_outcomes(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    metadata = {}

    async def pause():
        return {'paused': True}

    request = dict(owner_id='owner', chat_id='channel:continue-approval', chat_message_id='assistant',
                   user_message_id='user', model_id='model', request_payload={'messages': []}, handler=pause)
    try:
        await runtime.start(**request, metadata=metadata)
        with pytest.raises(HTTPException) as error:
            await runtime.start(**request, metadata={'assistant_message_id': 'assistant',
                                'continuation_id': '224e5d66-f3b5-48ac-9926-6d664a39f7db'})
        assert error.value.detail['code'] == 'continuation_not_allowed'
        assert (await repository.get_turn(metadata['agent_turn_id'])).status == 'waiting_approval'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
async def test_response_error_event_fails_run_even_when_handler_returns_normally(tmp_path, monkeypatch, streaming):
    from fastapi.responses import StreamingResponse
    from open_webui.utils.chat_runtime.response_context import ResponseContextAdapters, resolve_response_event_handlers

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
    metadata = {'chat_id': 'channel:response-error', 'message_id': 'assistant'}
    error = {'code': 'model_context_budget_exceeded', 'message': 'Input budget exceeded.'}

    async def emit(event):
        pass

    async def get_emitter(_):
        return emit

    async def handler():
        emitter, _ = await resolve_response_event_handlers(
            metadata, ResponseContextAdapters(get_emitter, None, None))

        async def body():
            await emitter({'type': 'chat:message:error', 'data': {'error': {'content': error}}})
            yield b'data: [DONE]\n\n'

        if streaming:
            return StreamingResponse(body())
        async for _ in body():
            pass

    try:
        response = await runtime.start(owner_id='owner', chat_id=metadata['chat_id'], chat_message_id='assistant',
                                       user_message_id='user', model_id='model', request_payload={'messages': []},
                                       metadata=metadata, handler=handler)
        if streaming:
            async for _ in response.body_iterator:
                pass
        turn = await repository.get_turn(metadata['agent_turn_id'])
        assert turn.status == 'failed'
        assert turn.error['code'] == 'model_context_budget_exceeded'
        events = await repository.list_events(turn.id)
        assert 'turn.completed' not in [event.event_type for event in events]
        assert events[-1].event_type == 'turn.failed'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_checkpoint_uses_run_metadata_instead_of_shared_request_state(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from open_webui.agent_runtime.gateway import ProviderGateway

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
    session = await repository.create_session('owner', 'channel:checkpoint')
    turn = await repository.create_turn(session.id, {'messages': []})
    await repository.claim_turn_by_id(turn.id, 'worker')
    metadata = {'agent_turn_id': turn.id, 'agent_turn_attempt': 1}
    gateway = ProviderGateway()
    prepared = gateway.prepare({'model': 'model', 'messages': [{'role': 'user', 'content': 'hello'}]},
                               'https://provider.invalid/v1')
    try:
        await gateway.checkpoint(SimpleNamespace(state=SimpleNamespace(metadata={})), prepared, metadata=metadata)
        checkpoint = await repository.latest_checkpoint(turn.id, kind='context')
        assert checkpoint.data['metrics']['estimated_input_tokens'] > 0
        assert checkpoint.data['payload']['messages'] == [{'role': 'user', 'content': 'hello'}]
        other = gateway.prepare({'model': 'model', 'messages': [{'role': 'user', 'content': 'title'}]},
                                'https://provider.invalid/v1')
        request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
        await gateway.checkpoint(request, other, metadata={})
        assert (await repository.latest_checkpoint(turn.id, kind='context')).version == checkpoint.version
        await gateway.checkpoint(request, other, metadata={**metadata, 'task': 'title_generation'})
        assert (await repository.latest_checkpoint(turn.id, kind='context')).version == checkpoint.version
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_budget_reserves_room_for_assistant_call_and_json_envelopes(tmp_path):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    session = await repository.create_session('owner', 'channel:headroom')
    turn = await repository.create_turn(session.id, {'messages': []})
    await repository.claim_turn_by_id(turn.id, 'worker')
    try:
        await repository.save_checkpoint(turn.id, {'metrics': {
            'serialized_chars': 94000, 'estimated_input_tokens': 10000,
            'context_window_tokens': 16000, 'reserved_output_tokens': 4096,
        }}, kind='context')
        limits = await runtime._remaining_tool_output_budget(turn.id)
        assert limits['max_tokens'] <= 1428
        assert limits['max_chars'] <= 488
    finally:
        await engine.dispose()


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
        run_attempt=1,
    )

    assert acquired is True
    full_result = 'S' + ('x' * 999_998) + 'E'
    preview = await runtime.complete_tool_call(turn.id, 'read-1', full_result, owner_id='owner', attempt=1)
    invocation = await repository.get_tool_call(turn.id, 'read-1')
    artifact = await artifacts.repository.get_artifact(invocation.result_artifact_id, owner_id='owner')
    cached, acquired_again = await runtime.begin_tool_call(
        turn.id,
        call_id='read-1',
        tool_name='read_file',
        arguments={'path': 'large.txt'},
        tool={'type': 'builtin'},
        run_attempt=1,
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
        run_attempt=1,
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
    preview = await runtime.complete_tool_call(turn.id, 'read-1', result, owner_id='owner', attempt=1)

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

    wrapped = runtime.wrap_event_emitter(turn.id, emitter, run_attempt=1, message_id='assistant-1')
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
        'agent_turn_attempt': 1,
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


@pytest.mark.asyncio
async def test_snapshot_watermark_covers_page_when_event_arrives_during_replay(tmp_path, monkeypatch):
    engine, repository = await _repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'channel:concurrent-snapshot')
        turn = await repository.create_turn(session.id, {'messages': []})
        original = repository.get_turn_status
        calls = 0

        async def status_then_append(*args, **kwargs):
            nonlocal calls
            status = await original(*args, **kwargs)
            calls += 1
            if calls == 1:
                await repository.append_event(turn.id, 'client.chat:completion', {
                    'snapshot_required': True, 'event_type': 'chat:completion', 'message_id': 'message-1',
                })
            return status

        monkeypatch.setattr(repository, 'get_turn_status', status_then_append)
        page = await AgentRuntime(repository, worker_id='worker').stream(turn.id, owner_id='owner')
        assert page['snapshot_required'] is True
        assert page['snapshot']['latest_sequence'] >= page['latest_sequence']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_recheck_fails_closed_if_turn_is_no_longer_owned(tmp_path, monkeypatch):
    engine, repository = await _repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'channel:revoked-snapshot')
        turn = await repository.create_turn(session.id, {'messages': []})
        await repository.append_event(turn.id, 'client.chat:completion', {'snapshot_required': True})
        original = repository.get_turn_status
        calls = 0

        async def remove_access_on_recheck(*args, **kwargs):
            nonlocal calls
            calls += 1
            return await original(*args, **kwargs) if calls == 1 else None

        monkeypatch.setattr(repository, 'get_turn_status', remove_access_on_recheck)
        with pytest.raises(HTTPException) as error:
            await AgentRuntime(repository, worker_id='worker').stream(turn.id, owner_id='owner')
        assert error.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replay_page_does_not_include_events_after_its_watermark(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    engine, repository = await _repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'channel:page-watermark')
        turn = await repository.create_turn(session.id, {'messages': []})
        original_scalars = AsyncSession.scalars
        inserted = False

        async def append_before_fetch(db, statement, *args, **kwargs):
            nonlocal inserted
            if not inserted:
                inserted = True
                await repository.append_event(turn.id, 'assistant.delta', {'text': 'concurrent'})
            return await original_scalars(db, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, 'scalars', append_before_fetch)
        page = await repository.get_event_page(turn.id, after_sequence=0)
        assert inserted
        assert page['latest_sequence'] == 1
        assert [event.sequence for event in page['events']] == [1]
        assert page['next_after_sequence'] == 1
        next_page = await repository.get_event_page(turn.id, after_sequence=1)
        assert [event.sequence for event in next_page['events']] == [2]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('old_outcome', ['success', 'exception'])
async def test_tool_execution_rejects_old_attempt_after_retry_starts(tmp_path, monkeypatch, old_outcome):
    """The production tool path propagates its original durable attempt token."""
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
    session = await repository.create_session('owner', 'channel:stale-tool')
    turn = await repository.create_turn(session.id, {'messages': []})
    await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1)
    old_started, new_started = asyncio.Event(), asyncio.Event()
    release_old, release_new = asyncio.Event(), asyncio.Event()
    calls = 0

    async def controlled_tool(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            await release_old.wait()
            if old_outcome == 'exception':
                raise RuntimeError('old attempt failed')
            return 'stale result'
        new_started.set()
        await release_new.wait()
        return 'current result'

    monkeypatch.setattr(tool_execution, '_invoke_tool', controlled_tool)
    tool = {'type': 'builtin', 'spec': {'parameters': {'properties': {}}}}
    metadata = {'agent_turn_id': turn.id, 'user_id': 'owner', 'chat_id': 'channel:stale-tool',
                'params': {'tool_approval_mode': 'full'}, 'tools': {'read_agent_artifact': tool}}

    def call(run_attempt):
        return tool_execution.execute_tool_call(
            {}, {**metadata, 'agent_turn_attempt': run_attempt}, None,
            {'id': 'call-1', 'call_id': 'call-1', 'name': 'read_agent_artifact', 'arguments': '{}'},
        )

    old = asyncio.create_task(call(1))
    new = None
    try:
        await asyncio.wait_for(old_started.wait(), timeout=10)
        assert await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5)) == 1
        await repository.claim_turn_by_id(turn.id, 'worker-new', lease_seconds=30)
        new = asyncio.create_task(call(2))
        await asyncio.wait_for(new_started.wait(), timeout=10)
        release_old.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(old, timeout=10)
        assert error.value.status_code == 409
        assert (await repository.get_tool_call(turn.id, 'call-1')).status == 'running'
        release_new.set()
        _, result, *_ = await asyncio.wait_for(new, timeout=10)
        assert result == 'current result'
        invocation = await repository.get_tool_call(turn.id, 'call-1')
        assert invocation.status == 'succeeded' and invocation.attempt_count == 2
        assert invocation.result_data == 'current result'
    finally:
        release_old.set()
        release_new.set()
        tasks = [task for task in (old, new) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_output_processing_finishing_after_recovery_cannot_publish_stale_result(tmp_path):
    """A worker that loses its lease during output processing cannot complete the retry."""
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    processing_started, release_processing = asyncio.Event(), asyncio.Event()
    manager = ToolOutputManager(max_tokens=1000)
    original_process = manager.process

    async def paused_process(*args, **kwargs):
        processing_started.set()
        await release_processing.wait()
        return await original_process(*args, **kwargs)

    manager.process = paused_process
    runtime = AgentRuntime(repository, output_manager=manager, worker_id='worker')
    session = await repository.create_session('owner', 'channel:output-race')
    turn = await repository.create_turn(session.id, {'messages': []})
    await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1)
    first, acquired = await runtime.begin_tool_call(
        turn.id, call_id='call-1', tool_name='read_agent_artifact', arguments={}, tool={'type': 'builtin'},
        run_attempt=1,
    )
    assert acquired
    task = asyncio.create_task(runtime.complete_tool_call(
        turn.id, 'call-1', 'old result', owner_id='owner', attempt=first.attempt_count
    ))
    try:
        await asyncio.wait_for(processing_started.wait(), timeout=10)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        await repository.claim_turn_by_id(turn.id, 'worker-new', lease_seconds=30)
        second, acquired = await runtime.begin_tool_call(
            turn.id, call_id='call-1', tool_name='read_agent_artifact', arguments={}, tool={'type': 'builtin'},
            run_attempt=2,
        )
        assert acquired and second.attempt_count == 2
        release_processing.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, timeout=10)
        assert error.value.status_code == 409
        current = await repository.get_tool_call(turn.id, 'call-1')
        assert current.status == 'running' and current.attempt_count == 2
        assert current.result_data is None
        result = await runtime.complete_tool_call(
            turn.id, 'call-1', 'new result', owner_id='owner', attempt=second.attempt_count
        )
        assert result == 'new result'
    finally:
        release_processing.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_execution_does_not_call_tool_after_cancel_request(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='worker')
        monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
        effect = AsyncMock(return_value='sent')
        monkeypatch.setattr(tool_execution, '_invoke_tool', effect)
        session = await repository.create_session('owner', 'channel:cancel-before-tool')
        turn = await repository.create_turn(session.id, {'messages': []})
        await repository.claim_turn_by_id(turn.id, 'worker')
        await repository.request_cancel_turn(turn.id, owner_id='owner')
        tool = {'type': 'builtin', 'spec': {'parameters': {'properties': {}}}}
        metadata = {'agent_turn_id': turn.id, 'agent_turn_attempt': 1, 'user_id': 'owner', 'chat_id': 'channel:cancel-before-tool',
                    'params': {'tool_approval_mode': 'full'}, 'tools': {'send_message': tool}}
        with pytest.raises(HTTPException) as error:
            await tool_execution.execute_tool_call(
                {}, metadata, None,
                {'id': 'call-1', 'call_id': 'call-1', 'name': 'send_message', 'arguments': '{}'},
            )
        assert error.value.status_code == 409
        effect.assert_not_awaited()
        assert await repository.get_tool_call(turn.id, 'call-1') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_run_attempt_cannot_start_new_side_effect_after_recovery(tmp_path, monkeypatch):
    """A recovered run must not let its old executor acquire a new side effect."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='worker-old')
        monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
        effect = AsyncMock(return_value='sent')
        monkeypatch.setattr(tool_execution, '_invoke_tool', effect)
        session = await repository.create_session('owner', 'channel:stale-executor')
        turn = await repository.create_turn(session.id, {'messages': []})
        old = await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        current = await repository.claim_turn_by_id(turn.id, 'worker-new', lease_seconds=30)
        assert old.attempts == 1 and current.attempts == 2
        tool = {'type': 'builtin', 'spec': {'parameters': {'properties': {}}}}
        metadata = {'agent_turn_id': turn.id, 'agent_turn_attempt': old.attempts, 'user_id': 'owner',
                    'chat_id': 'channel:stale-executor', 'params': {'tool_approval_mode': 'full'},
                    'tools': {'send_message': tool}}
        with pytest.raises(HTTPException) as error:
            await tool_execution.execute_tool_call(
                {}, metadata, None,
                {'id': 'late-call', 'call_id': 'late-call', 'name': 'send_message', 'arguments': '{}'},
            )
        assert error.value.status_code == 409
        effect.assert_not_awaited()
        assert await repository.get_tool_call(turn.id, 'late-call') is None
        for invalid_attempt in (None, True, 0):
            with pytest.raises(HTTPException) as invalid:
                await tool_execution.execute_tool_call(
                    {}, {**metadata, 'agent_turn_attempt': invalid_attempt}, None,
                    {'id': 'late-call', 'call_id': 'late-call', 'name': 'send_message', 'arguments': '{}'},
                )
            assert invalid.value.status_code == 409
        effect.assert_not_awaited()
        _, result, *_ = await tool_execution.execute_tool_call(
            {}, {**metadata, 'agent_turn_attempt': current.attempts}, None,
            {'id': 'late-call', 'call_id': 'late-call', 'name': 'send_message', 'arguments': '{}'},
        )
        assert result == 'sent'
        effect.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_handler_completion_does_not_fail_or_complete_new_claim(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-old')
    started, release = asyncio.Event(), asyncio.Event()
    metadata = {}

    async def old_handler():
        started.set()
        await release.wait()
        return {'answer': 'old'}

    task = asyncio.create_task(runtime.start(
        owner_id='owner', chat_id='channel:stale-completion', chat_message_id='message',
        user_message_id='user-message', model_id='test', request_payload={'messages': []},
        metadata=metadata, handler=old_handler,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        turn_id = metadata['agent_turn_id']
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(minutes=5))
        current = await repository.claim_turn_by_id(turn_id, 'worker-new', lease_seconds=30)
        release.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, timeout=10)
        assert error.value.status_code == 409
        still_running = await repository.get_turn(turn_id)
        assert still_running.status == 'running' and still_running.attempts == current.attempts
        assert still_running.worker_id == 'worker-new'
        assert (await repository.complete_turn(turn_id, run_attempt=current.attempts)).status == 'completed'
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_handler_error_cannot_fail_new_run_claim(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-old')
    started, release = asyncio.Event(), asyncio.Event()
    metadata = {}

    async def old_handler():
        started.set()
        await release.wait()
        raise RuntimeError('old executor failed')

    task = asyncio.create_task(runtime.start(
        owner_id='owner', chat_id='channel:stale-error', chat_message_id='message',
        user_message_id='user-message', model_id='test', request_payload={'messages': []},
        metadata=metadata, handler=old_handler,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        turn_id = metadata['agent_turn_id']
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(minutes=5))
        current = await repository.claim_turn_by_id(turn_id, 'worker-new', lease_seconds=30)
        release.set()
        with pytest.raises((RuntimeError, HTTPException)):
            await asyncio.wait_for(task, timeout=10)
        still_running = await repository.get_turn(turn_id)
        assert still_running.status == 'running' and still_running.attempts == current.attempts
        assert still_running.worker_id == 'worker-new'
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_handler_cancellation_does_not_interrupt_new_run_tool(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker-old')
    started = asyncio.Event()
    metadata = {}

    async def old_handler():
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(runtime.start(
        owner_id='owner', chat_id='channel:stale-cancel', chat_message_id='message',
        user_message_id='user-message', model_id='test', request_payload={'messages': []},
        metadata=metadata, handler=old_handler,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        turn_id = metadata['agent_turn_id']
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(minutes=5))
        current = await repository.claim_turn_by_id(turn_id, 'worker-new', lease_seconds=30)
        await repository.begin_tool_call(
            turn_id, call_id='new-tool', tool_name='read', arguments={}, retry_safe=True,
            run_attempt=current.attempts, idempotency_key='new-tool'
        )
        task.cancel()
        with pytest.raises((asyncio.CancelledError, HTTPException)):
            await asyncio.wait_for(task, timeout=10)
        assert (await repository.get_turn(turn_id)).status == 'running'
        assert (await repository.get_tool_call(turn_id, 'new-tool')).status == 'running'
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_tool_execution_cannot_record_approval_for_new_generation(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='worker-old')
        monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
        effect = AsyncMock(return_value='sent')
        monkeypatch.setattr(tool_execution, '_invoke_tool', effect)
        session = await repository.create_session('owner', 'channel:stale-approval')
        turn = await repository.create_turn(session.id, {'messages': []})
        old = await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        current = await repository.claim_turn_by_id(turn.id, 'worker-new', lease_seconds=30)
        tool = {'type': 'builtin', 'spec': {'parameters': {'properties': {}}}}
        metadata = {'agent_turn_id': turn.id, 'agent_turn_attempt': old.attempts, 'user_id': 'owner',
                    'chat_id': 'channel:stale-approval', 'params': {'tool_approval_mode': 'full'},
                    'tools': {'send_message': tool}}
        with pytest.raises(HTTPException) as error:
            await tool_execution.execute_tool_call(
                {}, metadata, None,
                {'id': 'call-1', 'call_id': 'call-1', 'name': 'send_message',
                 'arguments': '{}', 'approved': True},
            )
        assert error.value.status_code == 409
        effect.assert_not_awaited()
        assert await repository.get_tool_call(turn.id, 'call-1') is None
        _, result, *_ = await tool_execution.execute_tool_call(
            {}, {**metadata, 'agent_turn_attempt': current.attempts}, None,
            {'id': 'call-1', 'call_id': 'call-1', 'name': 'send_message',
             'arguments': '{}', 'approved': True},
        )
        assert result == 'sent'
        effect.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_handler_cannot_persist_or_emit_client_event(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='old')
        session = await repository.create_session('owner', 'channel:stale-event')
        turn = await repository.create_turn(session.id, {'messages': []})
        old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
        sent = []

        async def emitter(event):
            sent.append(event)

        wrapped = runtime.wrap_event_emitter(turn.id, emitter, run_attempt=old.attempts, message_id='message')
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
        before = len(await repository.list_events(turn.id))
        with pytest.raises(HTTPException) as error:
            await wrapped({'type': 'chat:completion', 'data': {'content': 'stale'}})
        assert error.value.status_code == 409
        assert sent == []
        assert len(await repository.list_events(turn.id)) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_event_is_durable_and_old_run_cannot_emit_it(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='old')
        session = await repository.create_session('owner', 'channel:status-event')
        turn = await repository.create_turn(session.id, {'messages': []})
        old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
        sent = []

        async def emitter(event):
            sent.append(event)

        wrapped = runtime.wrap_event_emitter(turn.id, emitter, run_attempt=old.attempts)
        await wrapped({'type': 'status', 'data': {'description': 'reading'}})
        events = await repository.list_events(turn.id)
        assert len(sent) == 1
        assert any(event.event_type == 'client.status' for event in events)
        assert sent[0]['runtime_event_sequence'] == events[-1].sequence

        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
        before = len(await repository.list_events(turn.id))
        with pytest.raises(HTTPException) as error:
            await wrapped({'type': 'status', 'data': {'description': 'stale'}})
        assert error.value.status_code == 409
        assert len(sent) == 1
        assert len(await repository.list_events(turn.id)) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_provider_request_cannot_bind_or_checkpoint_new_run(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from open_webui.agent_runtime.gateway import ProviderGateway

    engine, repository = await _repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id='old')
        monkeypatch.setattr(AgentRuntime, 'configured', classmethod(lambda cls: runtime))
        session = await repository.create_session('owner', 'channel:stale-checkpoint')
        turn = await repository.create_turn(session.id, {'messages': []})
        old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        current = await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
        request = SimpleNamespace(state=SimpleNamespace(metadata={
            'agent_turn_id': turn.id, 'agent_turn_attempt': old.attempts
        }))
        prepared = SimpleNamespace(payload={'model': 'test', 'messages': []}, metrics={})
        with pytest.raises(HTTPException) as error:
            await ProviderGateway().checkpoint(request, prepared)
        assert error.value.status_code == 409
        stored = await repository.get_turn(turn.id)
        assert stored.tool_contract_version == 'pending'
        assert stored.skill_snapshot_version == 'pending'
        assert await repository.latest_checkpoint(turn.id) is None
        request.state.metadata['agent_turn_attempt'] = current.attempts
        await ProviderGateway().checkpoint(request, prepared)
        saved = await repository.latest_checkpoint(turn.id, kind='context')
        assert saved is not None and saved.data['payload']['model'] == 'test'
        with pytest.raises(HTTPException) as stale_checkpoint:
            await repository.save_checkpoint(turn.id, {'payload': 'stale'}, kind='context', run_attempt=old.attempts)
        assert stale_checkpoint.value.status_code == 409
        assert (await repository.latest_checkpoint(turn.id, kind='context')).id == saved.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('metadata_key', 'metadata_value', 'expected_status'),
    [
        ('agent_waiting_user_input', {'call_id': 'call-1'}, 'waiting_user_input'),
        ('agent_waiting_approval', True, 'waiting_approval'),
    ],
)
async def test_streaming_emitter_pause_without_dict_return_is_resumable(
    tmp_path, metadata_key, metadata_value, expected_status
):
    engine, repository = await _repository(tmp_path)
    runtime = AgentRuntime(repository, worker_id='worker')
    metadata = {}
    phase = {'resumed': False}

    async def handler():
        if not phase['resumed']:
            metadata[metadata_key] = metadata_value
            return None
        return {'answer': 'resumed'}

    request = dict(
        owner_id='owner',
        chat_id='chat-pause-none',
        chat_message_id='assistant-1',
        user_message_id='user-1',
        model_id='model',
        request_payload={'messages': [{'role': 'user', 'content': 'hi'}]},
        handler=handler,
    )
    try:
        await runtime.run_chat_turn(**request, metadata=metadata)
        paused_turn = await repository.get_turn(metadata['agent_turn_id'])
        assert paused_turn.status == expected_status

        phase['resumed'] = True
        resume_metadata = {'tool_approval_resume': ('chat-pause-none', 'assistant-1', 'model')}
        await runtime.run_chat_turn(**request, metadata=resume_metadata)
        resumed_turn = await repository.get_turn(resume_metadata['agent_turn_id'])
        assert resumed_turn.id == paused_turn.id
        assert resumed_turn.status == 'completed'
    finally:
        await engine.dispose()
