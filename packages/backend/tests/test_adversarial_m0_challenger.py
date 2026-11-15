"""Adversarial stress-testing suite for AgentRuntime characterization and budget boundaries.

Milestone M0 Challenger:
1. Exact request budget boundaries (95,000 prep vs 95,001; 100,000 wire vs 100,001; UTF-8 byte bounds).
2. Monotonic sequence increments, gap detection logic, and snapshot inclusion.
3. Database isolation: ephemeral test database, zero external network leakage.
4. Tool ledger: ambiguous/unknown side-effect quarantine, blocking duplicates, and lease expiry recovery.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime.context import ContextManager, PreparedContext
from open_webui.agent_runtime.gateway import ProviderGateway
from open_webui.agent_runtime.models import (
    AgentArtifact,
    AgentCheckpoint,
    AgentEvent,
    AgentFileContent,
    AgentSession,
    AgentToolInvocation,
    AgentTurn,
    Base,
    utcnow,
)
from open_webui.agent_runtime.repository import AgentRepository
from open_webui.agent_runtime.request_budget import (
    DEFAULT_MAX_JSON_BYTES,
    DEFAULT_MAX_JSON_CHARS,
    DEFAULT_MAX_PREPARATION_CHARS,
    RequestBudgetExceeded,
    RequestBudgetLimits,
    RequestBudgetManager,
)
from open_webui.agent_runtime.runtime import AgentRuntime
from open_webui.mini_hermes.provider_errors import ProviderPayloadTooLarge
from open_webui.utils.json_codec import JSONCodec


async def _make_test_repository(tmp_path):
    """Create an isolated SQLite database in tmp_path with all runtime tables."""
    db_file = tmp_path / "adversarial_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as connection:
        tables = [
            AgentSession.__table__,
            AgentTurn.__table__,
            AgentEvent.__table__,
            AgentCheckpoint.__table__,
            AgentToolInvocation.__table__,
            AgentArtifact.__table__,
            AgentFileContent.__table__,
        ]
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = AgentRepository(session_factory)
    return engine, repository


# ==============================================================================
# CHALLENGE 1: Request Budget Boundaries (95,000 / 100,000 / UTF-8 Bytes)
# ==============================================================================


def _build_exact_single_turn_payload(target_serialized_chars: int) -> dict:
    """Construct a minimal single-turn payload that serializes to exactly target_chars."""
    # Base structure: {"model":"m","messages":[{"role":"user","content":""}]}
    base = {"model": "m", "messages": [{"role": "user", "content": ""}]}
    base_chars = len(JSONCodec.dumps(base))
    content_len = target_serialized_chars - base_chars
    assert content_len > 0, "Target length too small for base structure"
    payload = {"model": "m", "messages": [{"role": "user", "content": "x" * content_len}]}
    actual_len = len(JSONCodec.dumps(payload))
    assert actual_len == target_serialized_chars, f"Expected {target_serialized_chars}, got {actual_len}"
    return payload


def test_exact_95000_preparation_chars_accepted():
    """Verify that a request of exactly 95,000 preparation characters is accepted."""
    manager = ContextManager(
        request_budget=RequestBudgetManager(
            RequestBudgetLimits(
                max_json_chars=100_000,
                max_preparation_chars=95_000,
                max_json_bytes=400_000,
            )
        )
    )
    payload = _build_exact_single_turn_payload(95_000)
    capabilities = {"context_window_tokens": 128_000, "max_output_tokens": 1_000, "tool_calling": True}

    prepared = manager.prepare(payload, "https://provider.invalid/v1", capabilities)

    assert prepared.metrics["serialized_chars"] == 95_000
    assert prepared.metrics["compacted_messages"] == 0
    assert len(prepared.wire_body) == 95_000


def test_exact_95001_preparation_chars_rejected_when_single_turn():
    """Verify that a single-turn request of exactly 95,001 preparation characters is rejected."""
    manager = ContextManager(
        request_budget=RequestBudgetManager(
            RequestBudgetLimits(
                max_json_chars=100_000,
                max_preparation_chars=95_000,
                max_json_bytes=400_000,
            )
        )
    )
    payload = _build_exact_single_turn_payload(95_001)
    capabilities = {"context_window_tokens": 128_000, "max_output_tokens": 1_000, "tool_calling": True}

    with pytest.raises(ProviderPayloadTooLarge) as exc_info:
        manager.prepare(payload, "https://provider.invalid/v1", capabilities)

    assert exc_info.value.status_code == 413
    detail = exc_info.value.detail
    assert detail["code"] == "request_preparation_char_limit_exceeded"
    assert detail["preparation_limit_chars"] == 95_000
    assert detail["serialized_chars"] == 95_001


def test_95001_preparation_chars_triggers_turn_compaction_in_multi_turn():
    """Verify that when 95,001 chars occurs with multiple turns, old turn is compacted."""
    manager = ContextManager(
        request_budget=RequestBudgetManager(
            RequestBudgetLimits(
                max_json_chars=100_000,
                max_preparation_chars=95_000,
                max_json_bytes=400_000,
            )
        )
    )
    # Multi-turn payload exceeding 95,000 characters
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "old-turn-question " + "a" * 10_000},
            {"role": "assistant", "content": "old-turn-answer " + "b" * 10_000},
            {"role": "user", "content": "latest-question " + "c" * 80_000},
        ],
    }
    raw_len = len(JSONCodec.dumps(payload))
    assert raw_len > 95_000

    capabilities = {"context_window_tokens": 128_000, "max_output_tokens": 1_000, "tool_calling": True}
    prepared = manager.prepare(payload, "https://provider.invalid/v1", capabilities)

    # Old turn was compacted, new context stays within limit
    assert prepared.metrics["serialized_chars"] <= 95_000
    assert prepared.metrics["compacted_messages"] > 0
    # Latest user message is preserved
    assert any(m["role"] == "user" and "latest-question" in m["content"] for m in prepared.payload["messages"])


def test_exact_100000_wire_json_accepted_and_100001_rejected():
    """Verify that exact 100,000 wire JSON chars passes and 100,001 is rejected."""
    budget = RequestBudgetManager(
        RequestBudgetLimits(max_json_chars=100_000, max_preparation_chars=95_000, max_json_bytes=400_000)
    )
    wire_100k = '{"x":"' + ("x" * (100_000 - 8)) + '"}'
    wire_100k_plus_1 = '{"x":"' + ("x" * (100_001 - 8)) + '"}'

    assert len(wire_100k) == 100_000
    assert len(wire_100k_plus_1) == 100_001

    metrics = budget.validate(wire_100k)
    assert metrics["serialized_chars"] == 100_000

    with pytest.raises(RequestBudgetExceeded) as exc_info:
        budget.validate(wire_100k_plus_1)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_char_limit_exceeded"
    assert exc_info.value.detail["serialized_chars"] == 100_001


@pytest.mark.asyncio
async def test_oversized_wire_payload_blocks_network_before_call():
    """Verify that ProviderGateway.send rejects oversized body before any socket/session call."""
    gateway = ProviderGateway(
        request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_chars=100_000, max_json_bytes=400_000))
    )
    wire_oversized = '{"m":"' + ("x" * (100_001 - 8)) + '"}'
    assert len(wire_oversized) == 100_001

    prepared = PreparedContext(
        payload=JSONCodec.loads(wire_oversized),
        wire_body=wire_oversized,
        metrics={"serialized_chars": 100_001, "serialized_bytes": 100_001, "estimated_input_tokens": 1000},
    )

    mock_session = SimpleNamespace(request=AsyncMock())

    with pytest.raises(RequestBudgetExceeded) as exc_info:
        await gateway.send(mock_session, "POST", "https://provider.invalid/v1/chat/completions", prepared)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_char_limit_exceeded"
    mock_session.request.assert_not_called()


def test_utf8_byte_limit_boundary_enforcement():
    """Verify that byte limits (380,000 bytes) are strictly enforced even under 100k chars."""
    budget = RequestBudgetManager(
        RequestBudgetLimits(max_json_chars=100_000, max_json_bytes=380_000)
    )
    # Using 4-byte unicode character (e.g. '😀')
    # 95,001 chars of '😀' is 380,004 bytes (under 100k chars, but over 380k bytes)
    emoji_payload = '{"x":"' + ("😀" * 95_001) + '"}'
    assert len(emoji_payload) < 100_000
    assert len(emoji_payload.encode("utf-8")) > 380_000

    with pytest.raises(RequestBudgetExceeded) as exc_info:
        budget.validate(emoji_payload)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_byte_limit_exceeded"


def test_environment_variables_cannot_expand_approved_limits(monkeypatch):
    """Verify that environment variables attempting to raise limits above defaults are clamped."""
    monkeypatch.setenv("AGENT_REQUEST_MAX_JSON_CHARS", "200000")
    monkeypatch.setenv("AGENT_REQUEST_PREPARATION_MAX_JSON_CHARS", "150000")

    limits = RequestBudgetLimits.from_environment()
    assert limits.max_json_chars == DEFAULT_MAX_JSON_CHARS  # clamped to 100_000
    assert limits.preparation_limit_chars == DEFAULT_MAX_PREPARATION_CHARS  # clamped to 95_000


# ==============================================================================
# CHALLENGE 2: Event Replay & Gap Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_monotonic_sequence_and_uniqueness_constraint(tmp_path):
    """Verify sequence starts at 1, increments strictly monotonically, and enforces DB uniqueness."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})

        # Turn starts with sequence 1 (turn.queued)
        events = await repository.list_events(turn.id)
        assert len(events) == 1
        assert events[0].sequence == 1
        assert events[0].event_type == "turn.queued"

        # Sequential appends produce 2, 3, 4, 5
        for i in range(2, 6):
            ev = await repository.append_event(turn.id, "test.delta", {"step": i})
            assert ev.sequence == i

        # Adversarially attempt to insert duplicate sequence 3 for the same turn directly into DB
        async with repository._session_factory() as db:
            async with db.begin():
                dup_event = AgentEvent(
                    id="duplicate-uuid",
                    session_id=session.id,
                    turn_id=turn.id,
                    sequence=3,  # duplicate
                    event_type="test.duplicate",
                    payload={},
                )
                db.add(dup_event)
                with pytest.raises(IntegrityError):
                    await db.flush()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gap_detection_head_gap(tmp_path):
    """Verify that when the start of the event sequence is missing, gap is detected."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.append_event(turn.id, "test.delta", {"i": 2})
        await repository.append_event(turn.id, "test.delta", {"i": 3})

        # Prune event 1 (head gap)
        async with repository._session_factory() as db:
            await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == turn.id, AgentEvent.sequence == 1))
            await db.commit()

        # Query from cursor 0
        page = await repository.get_event_page(turn.id, after_sequence=0, limit=10)
        assert page["gap_detected"] is True
        assert page["snapshot_required"] is True
        assert [e.sequence for e in page["events"]] == [2, 3]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gap_detection_middle_gap(tmp_path):
    """Verify that when an intermediate event is missing, gap is detected."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.append_event(turn.id, "test.delta", {"i": 2})
        await repository.append_event(turn.id, "test.delta", {"i": 3})
        await repository.append_event(turn.id, "test.delta", {"i": 4})

        # Delete sequence 3 (middle gap: 1, 2, 4)
        async with repository._session_factory() as db:
            await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == turn.id, AgentEvent.sequence == 3))
            await db.commit()

        # Query from after_sequence=2
        page = await repository.get_event_page(turn.id, after_sequence=2, limit=10)
        assert page["gap_detected"] is True
        assert page["snapshot_required"] is True
        assert [e.sequence for e in page["events"]] == [4]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_contiguous_stream_has_no_gap(tmp_path):
    """Verify that a contiguous stream reports gap_detected=False and snapshot_required=False."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.append_event(turn.id, "test.delta", {"i": 2})
        await repository.append_event(turn.id, "test.delta", {"i": 3})

        page = await repository.get_event_page(turn.id, after_sequence=0, limit=10)
        assert page["gap_detected"] is False
        assert page["snapshot_required"] is False
        assert [e.sequence for e in page["events"]] == [1, 2, 3]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_stream_injects_snapshot_on_gap(tmp_path):
    """Verify that AgentRuntime.stream attaches full snapshot when a gap is detected."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id="test-worker")
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.append_event(turn.id, "test.delta", {"i": 2})

        # Contiguous check: snapshot not required
        clean_page = await runtime.stream(turn.id, owner_id="owner-1", after_sequence=0)
        assert clean_page["gap_detected"] is False
        assert clean_page["snapshot_required"] is False
        assert "snapshot" not in clean_page

        # Inject gap by deleting sequence 1
        async with repository._session_factory() as db:
            await db.execute(delete(AgentEvent).where(AgentEvent.turn_id == turn.id, AgentEvent.sequence == 1))
            await db.commit()

        # Replay should now include snapshot
        gapped_page = await runtime.stream(turn.id, owner_id="owner-1", after_sequence=0)
        assert gapped_page["gap_detected"] is True
        assert gapped_page["snapshot_required"] is True
        assert "snapshot" in gapped_page
        assert gapped_page["snapshot"]["turn_id"] == turn.id
        assert gapped_page["snapshot"]["status"] == turn.status
    finally:
        await engine.dispose()


# ==============================================================================
# CHALLENGE 3: Database Isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_database_isolation_operates_purely_in_sqlite(tmp_path):
    """Verify tests execute solely in isolated local SQLite, without external DB dependency."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        # Verify engine URL backend is sqlite
        assert engine.url.get_backend_name() == "sqlite"

        session = await repository.create_session("isolated-owner", "channel-iso")
        turn = await repository.create_turn(session.id, {"messages": []})
        assert turn.id is not None
        assert turn.session_id == session.id

        # Direct SQLite schema query to verify tables exist locally
        async with engine.begin() as conn:
            res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in res.fetchall()]
            assert "agent_session" in tables
            assert "agent_turn" in tables
            assert "agent_event" in tables
    finally:
        await engine.dispose()


# ==============================================================================
# CHALLENGE 4: Tool Ledger & Side-Effect Quarantine
# ==============================================================================


@pytest.mark.asyncio
async def test_side_effect_failure_quarantines_as_outcome_unknown_and_pauses_turn(tmp_path):
    """Verify that failing non-retry-safe tool marks invocation outcome_unknown and pauses turn."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.claim_turn_by_id(turn.id, "worker-1")

        # Begin a non-retry-safe side-effect tool call (e.g. send_message)
        invocation, acquired = await repository.begin_tool_call(
            turn.id,
            call_id="call-side-effect-1",
            tool_name="send_message",
            arguments={"text": "hello"},
            retry_safe=False,
            run_attempt=1,
        )
        assert acquired is True
        assert invocation.status == "running"

        # Fail tool with uncertain=True (ambiguous / remote disconnect)
        error_detail = {"code": "remote_disconnect", "message": "Connection lost during mutation"}
        failed_inv = await repository.fail_tool_call(
            turn.id,
            call_id="call-side-effect-1",
            error=error_detail,
            attempt=1,
            uncertain=True,
        )

        assert failed_inv.status == "outcome_unknown"

        # Verify turn is paused
        updated_turn = await repository.get_turn(turn.id)
        assert updated_turn.status == "paused"
        assert updated_turn.worker_id is None
        assert updated_turn.lease_until is None

        # Verify durable event emitted
        events = await repository.list_events(turn.id)
        assert any(e.event_type == "tool.outcome_unknown" and e.payload["call_id"] == "call-side-effect-1" for e in events)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quarantined_tool_call_blocks_duplicate_execution(tmp_path):
    """Verify that an invocation in outcome_unknown status blocks any retry attempt with HTTP 409."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        runtime = AgentRuntime(repository, worker_id="worker-1")
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.claim_turn_by_id(turn.id, "worker-1")

        # Initial begin with standard runtime idempotency key format
        _, acquired = await repository.begin_tool_call(
            turn.id,
            call_id="call-side-effect-2",
            tool_name="transfer_funds",
            arguments={"amount": 100},
            retry_safe=False,
            run_attempt=1,
            idempotency_key=f"{turn.id}:call-side-effect-2",
        )
        assert acquired is True

        # Fail with uncertain=True
        await repository.fail_tool_call(
            turn.id,
            call_id="call-side-effect-2",
            error={"code": "timeout"},
            attempt=1,
            uncertain=True,
        )

        # Invariant A: claim_turn_by_id strictly refuses to claim a turn with unresolved tool outcome
        with pytest.raises(HTTPException) as claim_err:
            await repository.claim_turn_by_id(turn.id, "worker-1")
        assert claim_err.value.status_code == 409
        assert "unresolved tool outcome blocks turn recovery" in claim_err.value.detail

        # Invariant B: Even if turn were set to running, begin_tool_call refuses duplicate invocation
        async with repository._session_factory() as db:
            async with db.begin():
                t = await db.get(AgentTurn, turn.id)
                t.status = "running"
                t.lease_until = utcnow() + dt.timedelta(minutes=5)
            await db.commit()

        tool_spec = {"type": "builtin", "spec": {"parameters": {"type": "object"}}}
        with pytest.raises(HTTPException) as exc_info:
            await runtime.begin_tool_call(
                turn.id,
                call_id="call-side-effect-2",
                tool_name="transfer_funds",
                arguments={"amount": 100},
                tool=tool_spec,
                run_attempt=1,
            )

        assert exc_info.value.status_code == 409
        assert "outcome is unknown" in exc_info.value.detail
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lease_expiry_recovery_quarantines_running_side_effects(tmp_path):
    """Verify stale turn recovery marks running side effects as outcome_unknown and pauses turn."""
    engine, repository = await _make_test_repository(tmp_path)
    try:
        session = await repository.create_session("owner-1", "chat-1")
        turn = await repository.create_turn(session.id, {"messages": []})
        await repository.claim_turn_by_id(turn.id, "worker-1")

        # Start a running side-effect tool
        await repository.begin_tool_call(
            turn.id,
            call_id="call-side-effect-3",
            tool_name="write_document",
            arguments={"content": "mutation"},
            retry_safe=False,
            run_attempt=1,
            idempotency_key=f"{turn.id}:call-side-effect-3",
        )

        # Force expire turn lease
        async with repository._session_factory() as db:
            await db.execute(text("UPDATE agent_turn SET lease_until = '2000-01-01 00:00:00' WHERE id = :id"), {"id": turn.id})
            await db.commit()

        # Run recovery for expired turns
        recovered_count = await repository.recover_expired_turns()
        assert recovered_count == 1

        # Verify invocation is quarantined
        inv = await repository.get_tool_call(turn.id, "call-side-effect-3")
        assert inv.status == "outcome_unknown"

        # Verify turn is paused
        recovered_turn = await repository.get_turn(turn.id)
        assert recovered_turn.status == "paused"

        # Verify recovery event
        events = await repository.list_events(turn.id)
        rec_events = [e for e in events if e.event_type == "turn.recovery_required"]
        assert len(rec_events) == 1
        assert "call-side-effect-3" in rec_events[0].payload["uncertain_tool_calls"]
    finally:
        await engine.dispose()
