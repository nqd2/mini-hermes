"""AgentRuntime characterization on an explicitly disposable PostgreSQL database.

Each test creates and drops a randomly named schema in MH_TEST_POSTGRES_URL.
Never point the environment variable at development or production data.
"""

import asyncio
import datetime as dt
import os
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime.models import (
    AgentArtifact, AgentCheckpoint, AgentEvent, AgentFileContent, AgentSession,
    AgentToolInvocation, AgentTurn, Base,
)
from open_webui.agent_runtime.repository import AgentRepository
from open_webui.agent_runtime.runtime import AgentRuntime


@pytest.fixture(scope='module')
def event_loop_policy():
    if sys.platform == 'win32':
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def pg_repository():
    database_url = os.getenv('MH_TEST_POSTGRES_URL')
    if not database_url:
        pytest.skip('Set MH_TEST_POSTGRES_URL to a disposable PostgreSQL database')
    url = make_url(database_url)
    if url.get_backend_name() not in {'postgresql', 'postgres'}:
        pytest.fail('MH_TEST_POSTGRES_URL must use PostgreSQL')
    if url.drivername == 'postgresql':
        url = url.set(drivername='postgresql+psycopg')
    schema = f'mh_test_{uuid4().hex}'
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    test_engine = None
    created = False
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        test_url = url.set(query={**dict(url.query), 'options': f'-csearch_path={schema}'})
        test_engine = create_async_engine(test_url, pool_pre_ping=True)
        async with test_engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                AgentSession.__table__, AgentTurn.__table__, AgentEvent.__table__,
                AgentCheckpoint.__table__, AgentToolInvocation.__table__, AgentArtifact.__table__,
                AgentFileContent.__table__,
            ]))
        sessions = async_sessionmaker(test_engine, expire_on_commit=False)
        yield AgentRepository(sessions), sessions
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        if created:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_parallel_starts_share_one_run_and_reject_changed_input(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    payload = {'messages': [{'role': 'user', 'content': 'hello'}]}
    first, second = await asyncio.gather(
        repository.create_turn(conversation.id, payload, idempotency_key='start-1'),
        repository.create_turn(conversation.id, payload, idempotency_key='start-1'),
    )
    assert first.id == second.id
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentEvent)) == 1
    with pytest.raises(HTTPException) as error:
        await repository.create_turn(conversation.id, {'messages': []}, idempotency_key='start-1')
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_parallel_event_appends_are_ordered_replayable_and_owner_scoped(pg_repository):
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    run = await repository.create_turn(conversation.id, {'messages': []})
    events = await asyncio.gather(*[
        repository.append_event(run.id, 'assistant.delta', {'item': index}) for index in range(12)
    ])
    assert sorted(event.sequence for event in events) == list(range(2, 14))
    runtime = AgentRuntime(repository, worker_id='test-worker')
    page = await runtime.stream(run.id, owner_id='owner-1', after_sequence=0, limit=100)
    assert [event.sequence for event in page['events']] == list(range(1, 14))
    assert page['gap_detected'] is False
    with pytest.raises(HTTPException) as error:
        await runtime.stream(run.id, owner_id='other-user', after_sequence=0)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_external_outcome_is_quarantined_after_worker_lease_expiry(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.claim_turn('worker-1', lease_seconds=1)
    calls = await asyncio.gather(*[
        repository.begin_tool_call(
            run.id, call_id='effect-1', tool_name='external_write',
            arguments={'value': 'x'}, retry_safe=False,
            run_attempt=1,
        ) for _ in range(2)
    ])
    assert sum(acquired for _, acquired in calls) == 1
    future = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
    assert await repository.recover_expired_turns(now=future) == 1
    assert (await repository.get_turn(run.id)).status == 'paused'
    assert (await repository.get_tool_call(run.id, 'effect-1')).status == 'outcome_unknown'
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AgentToolInvocation)) == 1


@pytest.mark.asyncio
async def test_event_foreign_key_rejects_missing_run(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    async with sessions() as db:
        db.add(AgentEvent(
            id=str(uuid4()), session_id=conversation.id, turn_id=str(uuid4()),
            sequence=1, event_type='invalid', payload={},
        ))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(AgentEvent)) == 0


@pytest.mark.asyncio
async def test_replay_gap_requires_owner_snapshot_and_rejects_changed_binding(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.bind_run_contract(run.id, tool_contract_version='tools-v1', skill_snapshot_version='skills-v1')
    with pytest.raises(HTTPException) as error:
        await repository.bind_run_contract(run.id, tool_contract_version='tools-v2', skill_snapshot_version='skills-v1')
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'runtime_snapshot_mismatch'

    await repository.append_event(run.id, 'assistant.delta', {'text': 'first'})
    await repository.append_event(run.id, 'assistant.delta', {'text': 'second'})
    async with sessions() as db:
        await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == run.id, AgentEvent.sequence == 2))
        await db.commit()
    runtime = AgentRuntime(repository, worker_id='test-worker')
    page = await runtime.stream(run.id, owner_id='owner-1', after_sequence=1)
    assert page['gap_detected'] is True
    assert page['snapshot_required'] is True
    assert page['snapshot']['turn_id'] == run.id
    assert [event.sequence for event in page['events']] == [3, 4]
    assert [event.payload['text'] for event in page['events']] == ['first', 'second']


@pytest.mark.asyncio
async def test_parallel_cancel_requests_are_owner_scoped_and_emit_once(pg_repository):
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-1')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.claim_turn_by_id(run.id, 'worker-1')
    first, second = await asyncio.gather(*[
        repository.request_cancel_turn(run.id, owner_id='owner-1') for _ in range(2)
    ])
    assert first.cancel_requested is True and second.cancel_requested is True
    assert [event.event_type for event in await repository.list_events(run.id)].count('turn.cancel_requested') == 1
    with pytest.raises(HTTPException) as error:
        await repository.request_cancel_turn(run.id, owner_id='other-user')
    assert error.value.status_code == 404

@pytest.mark.asyncio
async def test_approval_survives_repository_reopen_and_is_bound_to_exact_call(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-approval')
    run = await repository.create_turn(conversation.id, {'messages': []}, chat_message_id='message-approval')
    await repository.claim_turn_by_id(run.id, 'worker-1')
    await repository.set_turn_status(run.id, 'waiting_approval', run_attempt=1, event_type='turn.waiting_approval')
    approval = dict(owner_id='owner-1', call_id='call-1', tool_name='read_agent_artifact',
                    arguments={'artifact_id': 'artifact-1'}, decision='approved', approved_by='owner-1',
                    retry_safe=True, idempotency_key='approval-1')
    first = await repository.record_tool_approval(run.id, **approval)

    reopened = AgentRepository(sessions)
    duplicate = await reopened.record_tool_approval(run.id, **approval)
    assert duplicate.id == first.id
    assert duplicate.approval_status == 'approved'
    assert duplicate.approved_by == 'owner-1'
    with pytest.raises(HTTPException) as mismatch:
        await reopened.record_tool_approval(run.id, **{**approval, 'arguments': {'artifact_id': 'artifact-2'}})
    assert mismatch.value.status_code == 409
    with pytest.raises(HTTPException) as forbidden:
        await reopened.record_tool_approval(run.id, **{**approval, 'owner_id': 'other-user'})
    assert forbidden.value.status_code == 404
    events = await reopened.list_events(run.id)
    assert [event.event_type for event in events].count('tool.approved') == 1
    assert (await reopened.claim_turn_by_id(run.id, 'worker-2')).status == 'running'
    invocation, acquired = await reopened.begin_tool_call(
        run.id, call_id='call-1', tool_name='read_agent_artifact',
        arguments={'artifact_id': 'artifact-1'}, retry_safe=True, idempotency_key='approval-1',
        run_attempt=2,
    )
    assert acquired is True and invocation.status == 'running'


@pytest.mark.asyncio
async def test_runtime_resume_from_fresh_instance_preserves_turn_and_event_order(pg_repository):
    repository, sessions = pg_repository
    runtime = AgentRuntime(repository, worker_id='worker-before-reopen')
    metadata = {}
    handler_calls = []

    async def pause():
        handler_calls.append('pause')
        return {'status': True, 'paused': True}

    await runtime.start(
        owner_id='owner-1', chat_id='chat-resume', chat_message_id='message-1',
        user_message_id='user-message-1', model_id='model-a',
        request_payload={'messages': [{'role': 'user', 'content': 'hello'}]},
        metadata=metadata, handler=pause,
    )
    turn_id = metadata['agent_turn_id']
    assert (await repository.get_turn(turn_id)).status == 'waiting_approval'
    reopened_runtime = AgentRuntime(AgentRepository(sessions), worker_id='worker-after-reopen')

    async def finish():
        handler_calls.append('finish')
        return {'status': True}

    with pytest.raises(HTTPException) as denied:
        await reopened_runtime.resume(turn_id, owner_id='other-user', metadata={}, handler=finish)
    assert denied.value.status_code == 404
    assert handler_calls == ['pause']
    resumed_metadata = {}
    await reopened_runtime.resume(turn_id, owner_id='owner-1', metadata=resumed_metadata, handler=finish)
    assert resumed_metadata['agent_turn_id'] == turn_id
    assert handler_calls == ['pause', 'finish']
    assert (await repository.get_turn(turn_id)).status == 'completed'
    events = await repository.list_events(turn_id)
    assert [event.event_type for event in events] == [
        'turn.queued', 'turn.started', 'turn.waiting_approval', 'turn.started', 'turn.completed'
    ]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_user_question_wait_survives_runtime_restart(pg_repository):
    repository, sessions = pg_repository
    metadata = {}
    runtime = AgentRuntime(repository, worker_id='question-worker')

    async def ask():
        metadata['agent_waiting_user_input'] = {'call_id': 'ask-1'}
        return {'paused': True}

    await runtime.start(
        owner_id='owner-1', chat_id='chat-question', chat_message_id='message-1',
        user_message_id='user-message-1', model_id='model-a',
        request_payload={'messages': [{'role': 'user', 'content': 'choose'}]},
        metadata=metadata, handler=ask,
    )
    turn_id = metadata['agent_turn_id']
    status = await runtime.get_status(turn_id, owner_id='owner-1')
    assert status['status'] == 'waiting_user_input'
    assert [event.event_type for event in await repository.list_events(turn_id)][-1] == 'turn.waiting_user_input'

    reopened = AgentRuntime(AgentRepository(sessions), worker_id='resumed-question-worker')
    with pytest.raises(HTTPException) as denied:
        await reopened.resume(turn_id, owner_id='other-user', handler=lambda: None)
    assert denied.value.status_code == 404

    async def answer():
        return {'status': 'answered'}

    await reopened.resume(turn_id, owner_id='owner-1', handler=answer)
    assert (await reopened.get_status(turn_id, owner_id='owner-1'))['status'] == 'completed'


@pytest.mark.asyncio
async def test_streamed_user_question_is_persisted_before_replay(pg_repository):
    from starlette.responses import StreamingResponse

    repository, _ = pg_repository
    runtime = AgentRuntime(repository)
    metadata = {}

    async def chunks():
        yield b'data: question\n\n'

    async def ask():
        metadata['agent_waiting_user_input'] = {'call_id': 'ask-stream-1'}
        return StreamingResponse(chunks(), media_type='text/event-stream')

    response = await runtime.start(
        owner_id='owner-1', chat_id='chat-stream-question', chat_message_id='message-1',
        user_message_id='user-message-1', model_id='model-a',
        request_payload={'messages': [{'role': 'user', 'content': 'choose'}]},
        metadata=metadata, handler=ask,
    )
    assert [chunk async for chunk in response.body_iterator] == [b'data: question\n\n']
    status = await runtime.get_status(metadata['agent_turn_id'], owner_id='owner-1')
    assert status['status'] == 'waiting_user_input'
    page = await runtime.stream(metadata['agent_turn_id'], owner_id='owner-1')
    assert [event.event_type for event in page['events']][-1] == 'turn.waiting_user_input'

@pytest.mark.asyncio
@pytest.mark.parametrize('retry_safe', [True, False])
async def test_killed_worker_recovery_preserves_checkpoint_and_classifies_tool_outcome(
    pg_repository, tmp_path, retry_safe
):
    """Kill only a child created by this test after it commits its lease and tool call."""
    import subprocess

    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-process-recovery')
    run = await repository.create_turn(conversation.id, {'messages': []}, chat_message_id='message-1')
    ready = tmp_path / 'worker-ready'
    child_code = r'''
import asyncio
import os
import sys
import time
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from open_webui.agent_runtime.repository import AgentRepository

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def start():
    engine = create_async_engine(os.environ['MH_PHASE0_CHILD_TEST_DB'])
    repository = AgentRepository(async_sessionmaker(engine, expire_on_commit=False))
    run_id = os.environ['MH_PHASE0_CHILD_RUN']
    safe = os.environ['MH_PHASE0_CHILD_SAFE'] == '1'
    await repository.claim_turn_by_id(run_id, 'worker-killed', lease_seconds=1)
    await repository.save_checkpoint(run_id, {'completed_steps': ['step-1']})
    _, acquired = await repository.begin_tool_call(
        run_id, call_id='call-before-kill', tool_name='read_fixture' if safe else 'external_write',
        arguments={'id': 'fixture'}, retry_safe=safe, run_attempt=1,
    )
    if not acquired:
        raise RuntimeError('child did not acquire tool invocation')
    Path(os.environ['MH_PHASE0_CHILD_READY']).write_text('ready', encoding='utf-8')
    await asyncio.Event().wait()

asyncio.run(start())
'''
    child_env = os.environ.copy()
    child_env.update({
        'MH_PHASE0_CHILD_TEST_DB': sessions.kw['bind'].url.render_as_string(hide_password=False),
        'MH_PHASE0_CHILD_RUN': run.id,
        'MH_PHASE0_CHILD_SAFE': '1' if retry_safe else '0',
        'MH_PHASE0_CHILD_READY': str(ready),
    })
    child = subprocess.Popen([sys.executable, '-c', child_code], env=child_env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = asyncio.get_running_loop().time() + 30
        while not ready.exists():
            if child.poll() is not None:
                pytest.fail(f'worker exited before readiness with code {child.returncode}')
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail('worker did not reach its durable readiness barrier')
            await asyncio.sleep(0.05)
        child.kill()
        await asyncio.to_thread(child.wait, 10)
        assert child.returncode != 0
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait, 10)

    future = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
    assert await repository.recover_expired_turns(now=future) == 1
    checkpoint = await repository.latest_checkpoint(run.id)
    assert checkpoint.data == {'completed_steps': ['step-1']}
    invocation = await repository.get_tool_call(run.id, 'call-before-kill')
    recovered = await repository.get_turn(run.id)
    if retry_safe:
        assert invocation.status == 'pending' and recovered.status == 'interrupted'
        claimed = await repository.claim_turn_by_id(run.id, 'worker-replacement')
        assert claimed.status == 'running' and claimed.attempts == 2
    else:
        assert invocation.status == 'outcome_unknown' and recovered.status == 'paused'
        with pytest.raises(HTTPException) as blocked:
            await repository.claim_turn_by_id(run.id, 'worker-replacement')
        assert blocked.value.status_code == 409
    events = await repository.list_events(run.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

@pytest.mark.asyncio
async def test_failed_emitter_leaves_unpublished_event_in_durable_replay(pg_repository):
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-emitter-failure')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.claim_turn_by_id(run.id, 'worker')
    runtime = AgentRuntime(repository, worker_id='worker')

    async def failing_emitter(_event):
        raise ConnectionError('synthetic transport failure')

    wrapped = runtime.wrap_event_emitter(run.id, failing_emitter, run_attempt=1, message_id='message-1')
    with pytest.raises(ConnectionError):
        await wrapped({'type': 'chat:completion', 'data': {'done': True, 'content': 'persisted'}})
    replay = await runtime.stream(run.id, owner_id='owner-1', after_sequence=0)
    events = [event for event in replay['events'] if event.event_type == 'client.chat:completion']
    assert len(events) == 1
    assert events[0].published_at is None
    assert events[0].payload['event']['data']['content'] == 'persisted'
    assert replay['gap_detected'] is False


@pytest.mark.asyncio
async def test_oversized_event_has_per_event_snapshot_marker_in_replay(pg_repository):
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-large-event')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.claim_turn_by_id(run.id, 'worker')
    runtime = AgentRuntime(repository, worker_id='worker')
    delivered = []

    async def emitter(event):
        delivered.append(event)

    wrapped = runtime.wrap_event_emitter(run.id, emitter, run_attempt=1, message_id='message-1')
    await wrapped({'type': 'chat:completion', 'data': {'done': True, 'content': 'x' * 33000}})
    replay = await runtime.stream(run.id, owner_id='owner-1', after_sequence=0)
    event = next(event for event in replay['events'] if event.event_type == 'client.chat:completion')
    assert event.payload['snapshot_required'] is True
    assert event.payload['original_chars'] > 32000
    assert 'event' not in event.payload
    assert replay['snapshot_required'] is True
    assert replay['snapshot']['turn_id'] == run.id
    assert 'content' not in replay['snapshot']  # Status is not a reconstructed chat message.
    assert len(delivered[0]['data']['content']) == 33000

@pytest.mark.asyncio
async def test_gap_pagination_must_not_skip_unreturned_events_without_content_snapshot(pg_repository):
    """A status-only snapshot cannot justify advancing past unseen client events."""
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-gap-pages')
    run = await repository.create_turn(conversation.id, {'messages': []})
    for text_value in ('lost', 'first', 'second', 'third'):
        await repository.append_event(run.id, 'client.chat:completion', {'event': {'data': {'content': text_value}}})
    _, sessions = pg_repository
    async with sessions() as db:
        await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == run.id, AgentEvent.sequence == 2))
        await db.commit()
    runtime = AgentRuntime(repository, worker_id='worker')
    page = await runtime.stream(run.id, owner_id='owner-1', after_sequence=1, limit=1)
    assert page['gap_detected'] is True and page['snapshot_required'] is True
    assert [event.sequence for event in page['events']] == [3]
    assert page['has_more'] is True
    assert 'content' not in page['snapshot']
    # Without a content snapshot, continuation must not jump over sequences 4 and 5.
    assert page['next_after_sequence'] == 3
    following = await runtime.stream(run.id, owner_id='owner-1', after_sequence=page['next_after_sequence'], limit=10)
    assert [event.sequence for event in following['events']] == [4, 5]

@pytest.mark.asyncio
async def test_gap_with_no_available_events_keeps_cursor_and_requires_snapshot(pg_repository):
    repository, sessions = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-empty-gap')
    run = await repository.create_turn(conversation.id, {'messages': []})
    await repository.append_event(run.id, 'client.chat:completion', {'event': {'data': {'content': 'lost'}}})
    async with sessions() as db:
        await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == run.id, AgentEvent.sequence == 2))
        await db.commit()
    page = await AgentRuntime(repository, worker_id='worker').stream(run.id, owner_id='owner-1', after_sequence=1)
    assert page['events'] == []
    assert page['gap_detected'] is True and page['snapshot_required'] is True
    assert page['has_more'] is True and page['next_after_sequence'] == 1
    assert page['snapshot']['turn_id'] == run.id

@pytest.mark.asyncio
async def test_snapshot_watermark_does_not_precede_replay_page_after_concurrent_append(pg_repository, monkeypatch):
    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-snapshot-watermark')
    run = await repository.create_turn(conversation.id, {'messages': []})
    original = repository.get_turn_status
    calls = 0

    async def status_then_append(*args, **kwargs):
        nonlocal calls
        status = await original(*args, **kwargs)
        calls += 1
        if calls == 1:
            await repository.append_event(run.id, 'client.chat:completion', {
                'snapshot_required': True, 'event_type': 'chat:completion', 'message_id': 'message-1',
            })
        return status

    monkeypatch.setattr(repository, 'get_turn_status', status_then_append)
    page = await AgentRuntime(repository, worker_id='worker').stream(run.id, owner_id='owner-1', after_sequence=0)
    assert page['snapshot_required'] is True
    assert page['snapshot']['latest_sequence'] >= page['latest_sequence']


@pytest.mark.asyncio
async def test_replay_page_excludes_event_committed_after_watermark_postgres(pg_repository, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    repository, _ = pg_repository
    conversation = await repository.create_session('owner-1', 'chat-page-watermark')
    run = await repository.create_turn(conversation.id, {'messages': []})
    original_scalars = AsyncSession.scalars
    inserted = False

    async def append_before_fetch(db, statement, *args, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            await repository.append_event(run.id, 'assistant.delta', {'text': 'concurrent'})
        return await original_scalars(db, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, 'scalars', append_before_fetch)
    page = await repository.get_event_page(run.id, after_sequence=0)
    assert inserted and page['latest_sequence'] == 1
    assert [event.sequence for event in page['events']] == [1]
    assert page['next_after_sequence'] == 1
    next_page = await repository.get_event_page(run.id, after_sequence=1)
    assert [event.sequence for event in next_page['events']] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['run', 'tool', 'approval'])
async def test_json_boolean_and_number_cannot_share_idempotent_identity(pg_repository, boundary):
    repository, _ = pg_repository
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


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['complete', 'fail', 'release'])
async def test_tool_updates_do_not_hold_invocation_lock_while_waiting_for_turn(pg_repository, action):
    """Recovery locks turn first; tool updates must not acquire the locks in reverse order."""
    from sqlalchemy import event

    repository, sessions = pg_repository
    session = await repository.create_session('owner', f'lock-order-{action}')
    turn = await repository.create_turn(session.id, {'prompt': 'read'})
    await repository.claim_turn_by_id(turn.id, 'worker', lease_seconds=30)
    invocation, acquired = await repository.begin_tool_call(
        turn.id, call_id='call', tool_name='read', arguments={}, retry_safe=True, idempotency_key='key',
        run_attempt=1,
    )
    assert acquired
    holder = sessions()
    blocked_on_turn = asyncio.Event()
    engine = sessions.kw['bind'].sync_engine

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if 'FROM mh_agent_turn' in statement and 'FOR UPDATE' in statement:
            blocked_on_turn.set()

    async def update():
        if action == 'complete':
            return await repository.complete_tool_call(turn.id, 'call', 'result', attempt=invocation.attempt_count)
        if action == 'fail':
            return await repository.fail_tool_call(turn.id, 'call', {'code': 'error'}, attempt=invocation.attempt_count)
        return await repository.release_tool_call_for_retry(
            turn.id, 'call', {'code': 'retry'}, attempt=invocation.attempt_count
        )

    task = None
    try:
        await holder.begin()
        await holder.scalar(select(AgentTurn).where(AgentTurn.id == turn.id).with_for_update())
        event.listen(engine, 'before_cursor_execute', observe)
        task = asyncio.create_task(update())
        await asyncio.wait_for(blocked_on_turn.wait(), timeout=10)
        async with sessions() as probe:
            async with probe.begin():
                row = await probe.scalar(
                    select(AgentToolInvocation).where(AgentToolInvocation.id == invocation.id).with_for_update(nowait=True)
                )
                assert row.id == invocation.id
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
        await holder.rollback()
        await holder.close()
        if task is not None:
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_stale_run_cannot_overwrite_recovered_session_state_postgres(pg_repository):
    repository, _ = pg_repository
    session = await repository.create_session('owner', 'stale-session-pg')
    turn = await repository.create_turn(session.id, {'prompt': 'work'})
    old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
    await repository.recover_expired_turns(now=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5))
    current = await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
    await repository.update_session_turn_status(
        session.id, owner_id='owner', turn_id=turn.id, status='in_progress', run_attempt=current.attempts,
    )
    with pytest.raises(HTTPException) as error:
        await repository.update_session_turn_status(
            session.id, owner_id='owner', turn_id=turn.id, status='failed', run_attempt=old.attempts,
        )
    assert error.value.status_code == 409
    state = (await repository.get_session(session.id, owner_id='owner')).task_state
    assert state['status'] == 'in_progress' and turn.id in state['active_turn_ids']


@pytest.mark.asyncio
async def test_status_event_is_durable_and_stale_generation_is_fenced_postgres(pg_repository):
    repository, _ = pg_repository
    session = await repository.create_session('owner', 'status-event-pg')
    turn = await repository.create_turn(session.id, {'messages': []})
    old = await repository.claim_turn_by_id(turn.id, 'old', lease_seconds=1)
    runtime = AgentRuntime(repository, worker_id='old')
    delivered = []

    async def emit(event):
        delivered.append(event)

    wrapped = runtime.wrap_event_emitter(turn.id, emit, run_attempt=old.attempts)
    await wrapped({'type': 'status', 'data': {'description': 'reading'}})
    first = (await repository.list_events(turn.id))[-1]
    assert first.event_type == 'client.status'
    assert first.published_at is not None
    assert delivered[0]['runtime_event_sequence'] == first.sequence

    await repository.recover_expired_turns(now=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5))
    await repository.claim_turn_by_id(turn.id, 'new', lease_seconds=30)
    before = len(await repository.list_events(turn.id))
    with pytest.raises(HTTPException) as error:
        await wrapped({'type': 'status', 'data': {'description': 'stale'}})
    assert error.value.status_code == 409
    assert len(delivered) == 1
    assert len(await repository.list_events(turn.id)) == before
