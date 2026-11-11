from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime import AgentEvent, AgentRepository, Base
from open_webui.agent_runtime.contracts import (
    ConversationEngineBinding,
    RunBinding,
    compute_run_snapshot_versions,
)


async def make_repository(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "agent-contract.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = AgentRepository(async_sessionmaker(engine, expire_on_commit=False))
    return engine, repository


@pytest.mark.asyncio
async def test_conversation_engine_is_pinned_and_run_binding_is_versioned(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        original = ConversationEngineBinding(engine_type='agent-runtime', engine_version='v1')
        conversation = await repository.create_session('owner-1', 'chat-1', engine_binding=original)
        requested_change = await repository.create_session(
            'owner-1', 'chat-1', engine_binding=ConversationEngineBinding('deerflow', 'v2')
        )
        run = await repository.create_turn(
            conversation.id,
            {'model': 'test', 'messages': []},
            idempotency_key='start-1',
            tool_contract_version='pending',
            skill_snapshot_version='pending',
        )

        binding = RunBinding.from_records(requested_change, run)

        assert requested_change.engine_type == 'agent-runtime'
        assert requested_change.engine_version == 'v1'
        assert binding.engine_run_id == run.id
        assert binding.engine_thread_id == conversation.id
        assert binding.tool_contract_version == 'pending'
        assert binding.skill_snapshot_version == 'pending'
        assert binding.engine_type == requested_change.engine_type
        assert binding.engine_version == requested_change.engine_version
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_start_retry_is_idempotent_and_rejects_key_reuse_with_different_payload(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        conversation = await repository.create_session('owner-1', 'chat-1')
        first = await repository.create_turn(
            conversation.id, {'messages': [{'role': 'user', 'content': 'hello'}]}, idempotency_key='request-1'
        )
        retry = await repository.create_turn(
            conversation.id, {'messages': [{'role': 'user', 'content': 'hello'}]}, idempotency_key='request-1'
        )

        assert retry.id == first.id
        assert retry.sequence == first.sequence

        with pytest.raises(HTTPException) as error:
            await repository.create_turn(
                conversation.id, {'messages': [{'role': 'user', 'content': 'different'}]}, idempotency_key='request-1'
            )
        assert error.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_snapshot_is_frozen_and_resume_rejects_changed_contract(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        conversation = await repository.create_session('owner-1', 'chat-1')
        turn = await repository.create_turn(conversation.id, {'messages': []})

        first = await repository.bind_run_contract(
            turn.id,
            tool_contract_version='sha256:tools-v1',
            skill_snapshot_version='sha256:skills-v1',
        )
        retry = await repository.bind_run_contract(
            turn.id,
            tool_contract_version='sha256:tools-v1',
            skill_snapshot_version='sha256:skills-v1',
        )

        assert retry == first
        with pytest.raises(HTTPException) as error:
            await repository.bind_run_contract(
                turn.id,
                tool_contract_version='sha256:tools-v2',
                skill_snapshot_version='sha256:skills-v1',
            )
        assert error.value.status_code == 409
        assert error.value.detail['code'] == 'runtime_snapshot_mismatch'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_replay_reports_page_cursor_and_detects_sequence_gaps(tmp_path):
    engine, repository = await make_repository(tmp_path)
    try:
        conversation = await repository.create_session('owner-1', 'chat-1')
        turn = await repository.create_turn(conversation.id, {'messages': []})
        await repository.append_event(turn.id, 'assistant.delta', {'text': 'A'})
        await repository.append_event(turn.id, 'assistant.delta', {'text': 'B'})

        page = await repository.get_event_page(turn.id, after_sequence=0, limit=1)
        assert [event.sequence for event in page['events']] == [1]
        assert page['latest_sequence'] == 3
        assert page['next_after_sequence'] == 1
        assert page['has_more'] is True
        assert page['gap_detected'] is False

        async with repository._session_factory() as db:
            await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == turn.id, AgentEvent.sequence == 2))
            await db.commit()

        replay = await repository.get_event_page(turn.id, after_sequence=1, limit=10)
        assert [event.sequence for event in replay['events']] == [3]
        assert replay['gap_detected'] is True
        assert replay['snapshot_required'] is True
    finally:
        await engine.dispose()


def test_run_snapshot_versions_change_when_tools_or_instructions_change():
    baseline = {
        'tools': [{'name': 'read_document', 'parameters': {'type': 'object'}}],
        'messages': [{'role': 'system', 'content': 'Use approved tools only.'}],
    }
    tool_contract, skill_snapshot = compute_run_snapshot_versions(baseline)
    same_contract = compute_run_snapshot_versions(baseline)
    changed_tool = compute_run_snapshot_versions({
        **baseline,
        'tools': [{'name': 'commit_document', 'parameters': {'type': 'object'}}],
    })
    changed_instruction = compute_run_snapshot_versions({
        **baseline,
        'messages': [{'role': 'system', 'content': 'Ignore previous tool policy.'}],
    })

    assert same_contract == (tool_contract, skill_snapshot)
    assert changed_tool[0] != tool_contract
    assert changed_instruction[1] != skill_snapshot
