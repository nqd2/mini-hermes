from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import open_webui.agent_runtime as runtime


async def make_repository(tmp_path):
    assert hasattr(runtime, 'AgentRepository'), 'canonical AgentRepository is missing'
    assert hasattr(runtime, 'AgentSession'), 'durable AgentSession model is missing'
    assert hasattr(runtime, 'Base'), 'shared SQLAlchemy Base is missing'
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "agent-runtime.db"}')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    async with engine.begin() as connection:
        runtime_tables = [
            runtime.AgentSession.__table__,
            runtime.AgentTurn.__table__,
            runtime.AgentEvent.__table__,
            runtime.AgentCheckpoint.__table__,
            runtime.AgentToolInvocation.__table__,
            runtime.AgentArtifact.__table__,
            runtime.AgentFileContent.__table__,
        ]
        await connection.run_sync(
            lambda sync_connection: runtime.Base.metadata.create_all(sync_connection, tables=runtime_tables)
        )
    repository = runtime.AgentRepository(async_sessionmaker(engine, expire_on_commit=False))
    return engine, repository


@pytest.mark.asyncio
async def test_sessions_events_and_checkpoints_survive_repository_reopen(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-1')
        turn = await repository.create_turn(session.id, {'messages': [{'role': 'user', 'content': 'continue'}]})
        event = await repository.append_event(turn.id, 'assistant.delta', {'text': 'A'})
        first = await repository.save_checkpoint(turn.id, {'objective': 'continue', 'completed_steps': []})
        second = await repository.save_checkpoint(turn.id, {'objective': 'continue', 'completed_steps': ['A']})

        events = await repository.list_events(turn.id, after_sequence=0)
        restored = await repository.latest_checkpoint(turn.id)

        assert [item.sequence for item in events] == [1, 2]
        assert event.sequence == 2
        assert first.version == 1
        assert second.version == 2
        assert restored.data['completed_steps'] == ['A']
        assert await repository.get_session(session.id, owner_id='other-user') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_call_is_claimed_once_and_completed_call_is_never_replayed(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-2')
        turn = await repository.create_turn(session.id, {'prompt': 'read file'})
        await repository.claim_turn('worker-1', lease_seconds=30)

        first, acquired = await repository.begin_tool_call(
            turn.id,
            call_id='call-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='turn-call-1',
        )
        duplicate, duplicate_acquired = await repository.begin_tool_call(
            turn.id,
            call_id='call-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='turn-call-1',
        )
        await repository.complete_tool_call(turn.id, 'call-1', {'content': 'ok'})
        completed, completed_acquired = await repository.begin_tool_call(
            turn.id,
            call_id='call-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='turn-call-1',
        )

        assert acquired is True
        assert duplicate_acquired is False
        assert duplicate.status == 'running'
        assert completed_acquired is False
        assert completed.status == 'succeeded'
        assert first.id == duplicate.id == completed.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_turn_retries_idempotent_tool_but_pauses_unknown_side_effect(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        safe_session = await repository.create_session(owner_id='user-1', chat_id='chat-safe')
        safe_turn = await repository.create_turn(safe_session.id, {'prompt': 'read'})
        await repository.update_session_task_state(
            safe_session.id,
            owner_id='user-1',
            task_state={'status': 'in_progress', 'active_turn_id': safe_turn.id, 'active_turn_ids': [safe_turn.id]},
        )
        await repository.claim_turn('worker-1', lease_seconds=1)
        await repository.begin_tool_call(
            safe_turn.id,
            call_id='read-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='stable-read-1',
        )

        expiry = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert await repository.recover_expired_turns(now=expiry) == 1
        recovered_safe_turn = await repository.get_turn(safe_turn.id)
        safe_call = await repository.get_tool_call(safe_turn.id, 'read-1')
        recovered_session = await repository.get_session(safe_session.id, owner_id='user-1')
        await repository.claim_turn('worker-2', lease_seconds=30)
        _, can_retry = await repository.begin_tool_call(
            safe_turn.id,
            call_id='read-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='stable-read-1',
        )

        unsafe_session = await repository.create_session(owner_id='user-1', chat_id='chat-unsafe')
        unsafe_turn = await repository.create_turn(unsafe_session.id, {'prompt': 'send'})
        await repository.claim_turn('worker-3', lease_seconds=1)
        await repository.begin_tool_call(
            unsafe_turn.id,
            call_id='send-1',
            tool_name='send_message',
            arguments={'to': 'recipient'},
            retry_safe=False,
        )
        await repository.recover_expired_turns(now=expiry)

        recovered_unsafe_turn = await repository.get_turn(unsafe_turn.id)
        unsafe_call = await repository.get_tool_call(unsafe_turn.id, 'send-1')

        assert recovered_safe_turn.status == 'interrupted'
        assert safe_call.status == 'pending'
        assert safe_call.idempotency_key == 'stable-read-1'
        assert can_retry is True
        assert recovered_session.task_state['active_turn_ids'] == []
        assert recovered_session.task_state['status'] == 'interrupted'
        assert recovered_unsafe_turn.status == 'paused'
        assert unsafe_call.status == 'outcome_unknown'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_turns_can_resume_by_message_id_and_replay_ordered_events(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='owner', chat_id='chat')
        turn = await repository.create_turn(
            session.id,
            {'model': 'test', 'messages': []},
            model_id='test',
            chat_message_id='assistant-message',
        )
        await repository.claim_turn_by_id(turn.id, 'worker-a')
        await repository.set_turn_status(turn.id, 'waiting_approval', event_type='turn.waiting_approval')

        resumable = await repository.find_resumable_turn(session.id, 'assistant-message')
        assert resumable.id == turn.id
        resumed = await repository.claim_turn_by_id(turn.id, 'worker-b')
        assert resumed.status == 'running'
        events = await repository.list_events(turn.id)
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert events[-1].event_type == 'turn.started'
        await repository.complete_turn(turn.id)
        assert await repository.find_resumable_turn(session.id, 'assistant-message') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_turns_resume_by_assistant_message_id_and_replay_ordered_events(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-resume')
        turn = await repository.create_turn(
            session.id,
            {'messages': [{'role': 'user', 'content': 'continue'}]},
            model_id='test',
            chat_message_id='assistant-message',
        )
        await repository.claim_turn_by_id(turn.id, 'worker-1')
        await repository.set_turn_status(turn.id, 'waiting_approval', event_type='turn.waiting_approval')

        resumable = await repository.find_resumable_turn(session.id, 'assistant-message')
        assert resumable.id == turn.id
        resumed = await repository.claim_turn_by_id(turn.id, 'worker-2')
        assert resumed.status == 'running'
        events = await repository.list_events(turn.id)
        assert [item.sequence for item in events] == [1, 2, 3, 4]
        assert events[-1].event_type == 'turn.started'
        await repository.complete_turn(turn.id)
        assert await repository.find_resumable_turn(session.id, 'assistant-message') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_approval_is_durable_before_resume_and_authorizes_only_matching_call(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-approval')
        turn = await repository.create_turn(session.id, {'messages': []}, chat_message_id='assistant-approval')
        await repository.claim_turn_by_id(turn.id, 'worker-1')
        await repository.set_turn_status(turn.id, 'waiting_approval', event_type='turn.waiting_approval')

        approved = await repository.record_tool_approval(
            turn.id,
            owner_id='user-1',
            call_id='approved-call',
            tool_name='read_agent_artifact',
            arguments={'artifact_id': 'artifact-1'},
            decision='approved',
            approved_by='user-1',
            retry_safe=True,
            idempotency_key=f'{turn.id}:approved-call',
        )
        resumed_turn = await repository.claim_turn_by_id(turn.id, 'worker-2')
        running, acquired = await repository.begin_tool_call(
            turn.id,
            call_id='approved-call',
            tool_name='read_agent_artifact',
            arguments={'artifact_id': 'artifact-1'},
            retry_safe=True,
            idempotency_key=f'{turn.id}:approved-call',
        )

        assert approved.status == 'pending'
        assert approved.approval_status == 'approved'
        assert approved.approved_by == 'user-1'
        assert resumed_turn.status == 'running'
        assert acquired is True
        assert running.status == 'running'
        assert running.retry_safe is True

        rejected = await repository.record_tool_approval(
            turn.id,
            owner_id='user-1',
            call_id='rejected-call',
            tool_name='delete_file',
            arguments={'path': 'report.txt'},
            decision='rejected',
            approved_by='user-1',
            retry_safe=False,
            idempotency_key=f'{turn.id}:rejected-call',
        )
        assert rejected.status == 'failed'
        assert rejected.approval_status == 'rejected'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_is_owned_durable_and_idempotent(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-cancel')
        turn = await repository.create_turn(session.id, {'prompt': 'long task'})
        await repository.claim_turn_by_id(turn.id, 'worker-1')

        requested = await repository.request_cancel_turn(turn.id, owner_id='user-1')
        repeated = await repository.request_cancel_turn(turn.id, owner_id='user-1')
        events = await repository.list_events(turn.id)

        assert requested.status == repeated.status == 'running'
        assert requested.cancel_requested is True
        assert [event.event_type for event in events].count('turn.cancel_requested') == 1
        assert await repository.is_cancel_requested(turn.id) is True
        assert await repository.get_turn_status(turn.id, owner_id='other') is None
        status = await repository.get_turn_status(turn.id, owner_id='user-1')
        assert status['status'] == 'running'
        assert status['latest_sequence'] == events[-1].sequence
    finally:
        await engine.dispose()
