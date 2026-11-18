from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import weai.agent_runtime as runtime


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
            run_attempt=1,
        )
        duplicate, duplicate_acquired = await repository.begin_tool_call(
            turn.id,
            call_id='call-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='turn-call-1',
            run_attempt=1,
        )
        await repository.complete_tool_call(turn.id, 'call-1', {'content': 'ok'}, attempt=first.attempt_count)
        completed, completed_acquired = await repository.begin_tool_call(
            turn.id,
            call_id='call-1',
            tool_name='read_file',
            arguments={'path': 'a.py'},
            retry_safe=True,
            idempotency_key='turn-call-1',
            run_attempt=1,
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
            run_attempt=1,
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
            run_attempt=2,
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
            run_attempt=1,
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
        await repository.set_turn_status(turn.id, 'waiting_approval', run_attempt=1, event_type='turn.waiting_approval')

        resumable = await repository.find_resumable_turn(session.id, 'assistant-message')
        assert resumable.id == turn.id
        resumed = await repository.claim_turn_by_id(turn.id, 'worker-b')
        assert resumed.status == 'running'
        events = await repository.list_events(turn.id)
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert events[-1].event_type == 'turn.started'
        await repository.complete_turn(turn.id, run_attempt=resumed.attempts)
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
        await repository.set_turn_status(turn.id, 'waiting_approval', run_attempt=1, event_type='turn.waiting_approval')

        resumable = await repository.find_resumable_turn(session.id, 'assistant-message')
        assert resumable.id == turn.id
        resumed = await repository.claim_turn_by_id(turn.id, 'worker-2')
        assert resumed.status == 'running'
        events = await repository.list_events(turn.id)
        assert [item.sequence for item in events] == [1, 2, 3, 4]
        assert events[-1].event_type == 'turn.started'
        await repository.complete_turn(turn.id, run_attempt=resumed.attempts)
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
        await repository.set_turn_status(turn.id, 'waiting_approval', run_attempt=1, event_type='turn.waiting_approval')

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
            run_attempt=resumed_turn.attempts,
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


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['run', 'tool', 'approval'])
async def test_idempotent_identity_rejects_boolean_number_mismatch(tmp_path, boundary):
    """JSON true and 1 must not identify the same request or approved operation."""
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id=f'chat-type-{boundary}')
        original = {'nested': [{'flag': True}]}
        altered = {'nested': [{'flag': 1}]}
        if boundary == 'run':
            await repository.create_turn(session.id, original, idempotency_key='same-key')
            with pytest.raises(HTTPException) as error:
                await repository.create_turn(session.id, altered, idempotency_key='same-key')
        else:
            turn = await repository.create_turn(session.id, {'prompt': 'test'})
            await repository.claim_turn('worker-1', lease_seconds=30)
            if boundary == 'tool':
                await repository.begin_tool_call(turn.id, call_id='same-call', tool_name='effect',
                                                 arguments=original, retry_safe=False, idempotency_key='call-key', run_attempt=1)
                with pytest.raises(HTTPException) as error:
                    await repository.begin_tool_call(turn.id, call_id='same-call', tool_name='effect',
                                                     arguments=altered, retry_safe=False, idempotency_key='call-key', run_attempt=1)
            else:
                fields = dict(owner_id='user-1', call_id='same-call', tool_name='effect', decision='approved',
                              approved_by='user-1', retry_safe=False, idempotency_key='call-key')
                await repository.record_tool_approval(turn.id, arguments=original, **fields)
                with pytest.raises(HTTPException) as error:
                    await repository.record_tool_approval(turn.id, arguments=altered, **fields)
        assert error.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain', [False, True])
async def test_late_tool_failure_cannot_overwrite_success(tmp_path, uncertain):
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-late-failure')
        turn = await repository.create_turn(session.id, {'prompt': 'test'})
        await repository.claim_turn_by_id(turn.id, 'worker-1')
        await repository.begin_tool_call(turn.id, call_id='call-1', tool_name='read',
                                         arguments={}, retry_safe=True, idempotency_key='call-key', run_attempt=1)
        await repository.complete_tool_call(turn.id, 'call-1', {'value': 'verified'}, attempt=1)
        with pytest.raises(HTTPException) as error:
            await repository.fail_tool_call(turn.id, 'call-1', {'code': 'late'}, attempt=1, uncertain=uncertain)
        assert error.value.status_code == 409
        result = await repository.get_tool_call(turn.id, 'call-1')
        assert result.status == 'succeeded'
        assert result.result_data == {'value': 'verified'}
        assert (await repository.get_turn(turn.id)).status == 'running'
        assert [event.event_type for event in await repository.list_events(turn.id)].count('tool.completed') == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('late_action', ['complete', 'fail', 'release'])
async def test_stale_attempt_cannot_modify_retried_tool_call(tmp_path, late_action):
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session(owner_id='user-1', chat_id='chat-stale-attempt')
        turn = await repository.create_turn(session.id, {'prompt': 'read'})
        await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1)
        args = dict(call_id='read-1', tool_name='read_file', arguments={'path': 'a'},
                    retry_safe=True, idempotency_key='read-key')
        old, acquired = await repository.begin_tool_call(turn.id, run_attempt=1, **args)
        assert acquired and old.attempt_count == 1
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        await repository.claim_turn_by_id(turn.id, 'worker-new', lease_seconds=30)
        current, acquired = await repository.begin_tool_call(turn.id, run_attempt=2, **args)
        assert acquired and current.attempt_count == 2
        with pytest.raises(HTTPException) as error:
            if late_action == 'complete':
                await repository.complete_tool_call(turn.id, 'read-1', {'content': 'stale'}, attempt=old.attempt_count)
            elif late_action == 'fail':
                await repository.fail_tool_call(turn.id, 'read-1', {'code': 'stale'}, attempt=old.attempt_count)
            else:
                await repository.release_tool_call_for_retry(turn.id, 'read-1', {'code': 'stale'}, attempt=old.attempt_count)
        assert error.value.status_code == 409
        fresh = await repository.get_tool_call(turn.id, 'read-1')
        assert fresh.status == 'running' and fresh.attempt_count == 2
        assert (await repository.get_turn(turn.id)).status == 'running'
        result = await repository.complete_tool_call(turn.id, 'read-1', {'content': 'fresh'}, attempt=current.attempt_count)
        assert result.result_data == {'content': 'fresh'}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['complete', 'fail', 'release'])
async def test_tool_update_acquires_turn_before_invocation(tmp_path, action):
    """All mutations use recovery's turn-then-invocation lock order."""
    engine, repository = await make_repository(tmp_path)
    statements = []
    try:
        session = await repository.create_session('owner', f'lock-order-{action}')
        turn = await repository.create_turn(session.id, {'prompt': 'read'})
        await repository.claim_turn_by_id(turn.id, 'worker')
        invocation, _ = await repository.begin_tool_call(
            turn.id, call_id='call', tool_name='read', arguments={}, retry_safe=True, idempotency_key='key',
            run_attempt=1,
        )

        def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
            sql = ' '.join(statement.lower().split())
            if sql.startswith('select') and ('from ar_agent_turn ' in sql or 'from ar_agent_tool_invocation ' in sql):
                statements.append(sql)

        event.listen(engine.sync_engine, 'before_cursor_execute', observe)
        try:
            if action == 'complete':
                await repository.complete_tool_call(turn.id, 'call', 'result', attempt=invocation.attempt_count)
            elif action == 'fail':
                await repository.fail_tool_call(turn.id, 'call', {'code': 'error'}, attempt=invocation.attempt_count)
            else:
                await repository.release_tool_call_for_retry(
                    turn.id, 'call', {'code': 'retry'}, attempt=invocation.attempt_count
                )
        finally:
            event.remove(engine.sync_engine, 'before_cursor_execute', observe)
        assert 'from ar_agent_turn ' in statements[0]
        assert 'from ar_agent_tool_invocation ' in statements[1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_tool_call_is_not_started_after_durable_cancellation_requested(tmp_path):
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'cancel-before-tool')
        turn = await repository.create_turn(session.id, {'prompt': 'cancel'})
        await repository.claim_turn_by_id(turn.id, 'worker')
        await repository.request_cancel_turn(turn.id, owner_id='owner')
        with pytest.raises(HTTPException) as error:
            await repository.begin_tool_call(
                turn.id, call_id='after-cancel', tool_name='effect', arguments={},
                retry_safe=False, idempotency_key='after-cancel',
                run_attempt=1,
            )
        assert error.value.status_code == 409
        assert await repository.get_tool_call(turn.id, 'after-cancel') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_cannot_start_tool_or_be_renewed(tmp_path):
    """Expiry is a fence even before a recovery worker marks the turn interrupted."""
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'expired-lease')
        turn = await repository.create_turn(session.id, {'prompt': 'test'})
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        claimed = await repository.claim_turn_by_id(turn.id, 'worker-old', lease_seconds=1, now=past)
        with pytest.raises(HTTPException) as error:
            await repository.begin_tool_call(
                turn.id, call_id='late', tool_name='side_effect', arguments={}, retry_safe=False,
                run_attempt=claimed.attempts, idempotency_key='late-key'
            )
        assert error.value.status_code == 409
        assert await repository.get_tool_call(turn.id, 'late') is None
        assert await repository.heartbeat_turn(turn.id, 'worker-old', run_attempt=claimed.attempts, lease_seconds=30) is False
        assert await repository.recover_expired_turns() == 1
        assert (await repository.get_turn(turn.id)).status == 'interrupted'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_lease_only_renews_for_its_owner(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'live-lease')
        turn = await repository.create_turn(session.id, {'prompt': 'test'})
        clock = datetime.now(timezone.utc)
        await repository.claim_turn_by_id(turn.id, 'worker', lease_seconds=10, now=clock)
        assert not await repository.heartbeat_turn(turn.id, 'other', run_attempt=1, lease_seconds=30, now=clock + timedelta(seconds=1))
        assert await repository.heartbeat_turn(turn.id, 'worker', run_attempt=1, lease_seconds=30, now=clock + timedelta(seconds=1))
        assert not await repository.heartbeat_turn(turn.id, 'worker', run_attempt=1, lease_seconds=30, now=clock + timedelta(seconds=31))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_generation_cannot_heartbeat_new_claim_of_same_worker(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'same-worker-generation')
        turn = await repository.create_turn(session.id, {'prompt': 'test'})
        clock = datetime.now(timezone.utc)
        old = await repository.claim_turn_by_id(turn.id, 'worker', lease_seconds=1, now=clock)
        assert await repository.recover_expired_turns(now=clock + timedelta(seconds=2)) == 1
        current = await repository.claim_turn_by_id(turn.id, 'worker', lease_seconds=10, now=clock + timedelta(seconds=2))
        assert old.attempts == 1 and current.attempts == 2
        assert not await repository.heartbeat_turn(
            turn.id, 'worker', run_attempt=old.attempts, lease_seconds=30, now=clock + timedelta(seconds=3)
        )
        assert await repository.heartbeat_turn(
            turn.id, 'worker', run_attempt=current.attempts, lease_seconds=30, now=clock + timedelta(seconds=3)
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_run_cannot_complete_a_new_generation(tmp_path):
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'stale-run-completion')
        turn = await repository.create_turn(session.id, {'prompt': 'work'})
        old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        current = await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
        assert old.attempts == 1 and current.attempts == 2
        with pytest.raises(HTTPException) as error:
            await repository.complete_turn(turn.id, run_attempt=old.attempts)
        assert error.value.status_code == 409
        assert (await repository.get_turn(turn.id)).status == 'running'
        result = await repository.complete_turn(turn.id, run_attempt=current.attempts)
        assert result.status == 'completed'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_run_cannot_overwrite_new_session_task_state(tmp_path):
    from fastapi import HTTPException

    engine, repository = await make_repository(tmp_path)
    try:
        session = await repository.create_session('owner', 'stale-session-state')
        turn = await repository.create_turn(session.id, {'prompt': 'work'})
        old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
        await repository.recover_expired_turns(now=datetime.now(timezone.utc) + timedelta(seconds=5))
        current = await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
        assert old.attempts == 1 and current.attempts == 2
        await repository.update_session_turn_status(session.id, owner_id='owner', turn_id=turn.id, status='in_progress', run_attempt=current.attempts)
        with pytest.raises(HTTPException) as error:
            await repository.update_session_turn_status(session.id, owner_id='owner', turn_id=turn.id, status='failed', run_attempt=old.attempts)
        assert error.value.status_code == 409
        state = (await repository.get_session(session.id, owner_id='owner')).task_state
        assert state['status'] == 'in_progress' and turn.id in state['active_turn_ids']
    finally:
        await engine.dispose()
