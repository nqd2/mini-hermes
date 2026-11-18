"""Milestone M3.4 & M3.5 Tests: AgentRuntime V2 Persistence Replatforming, Request Budgets, Retry Policy, and Quarantine."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from weai.agent_runtime.context import ContextManager
from weai.agent_runtime.gateway import ProviderGateway
from weai.agent_runtime.request_budget import (
    DEFAULT_MAX_JSON_CHARS,
    DEFAULT_MAX_PREPARATION_CHARS,
    RequestBudgetExceeded,
    RequestBudgetLimits,
    RequestBudgetManager,
)
from weai.persistence.v2.models import V2Base
from weai.persistence.v2.models.runtime import WeAIRunEvent
from weai.persistence.v2.runtime import (
    FencingTokenMismatchError,
    V2AgentRepository,
    V2AgentRuntime,
    execute_v2_tool_with_retry,
)
from weai.persistence.v2.tools import ToolError


async def make_v2_runtime(
    tmp_path,
    *,
    worker_id: str = "worker-a",
    lease_seconds: int = 60,
) -> tuple[object, async_sessionmaker[AsyncSession], V2AgentRepository, V2AgentRuntime]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v2_m3_runtime.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(V2Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = V2AgentRepository(factory)
    rt = V2AgentRuntime(repo, worker_id=worker_id, lease_seconds=lease_seconds)
    return engine, factory, repo, rt


@pytest.mark.asyncio
async def test_v2_runtime_contract_bindings_fencing_and_v1_checkpoint_rejection(tmp_path):
    engine, _, repo, rt_a = await make_v2_runtime(tmp_path, worker_id="worker-a", lease_seconds=30)
    try:
        conv_id = str(uuid.uuid4())
        # 1. Reject V1 engine binding on V2 persistence
        with pytest.raises(HTTPException) as v1_exc:
            await repo.ensure_conversation_binding(conv_id, engine_version="v1")
        assert v1_exc.value.status_code == 409
        assert v1_exc.value.detail["code"] == "legacy_engine_rejected"

        # 2. Start V2 run idempotently
        meta: dict = {}
        payload = {"model": "gpt-4.1", "messages": [{"role": "user", "content": "Phân tích bảng doanh thu"}]}

        async def paused_handler():
            return {"paused": True}

        res1 = await rt_a.start(
            conversation_id=conv_id,
            owner_id="user-1",
            request_payload=payload,
            idempotency_key="start-key-1",
            chat_message_id="msg-1",
            user_message_id="umsg-1",
            metadata=meta,
            handler=paused_handler,
        )
        assert res1 == {"paused": True}
        run_id = meta["v2_run_id"]
        assert meta["v2_fencing_token"] == 1

        status = await rt_a.get_status(run_id, owner_id="user-1")
        assert status["status"] == "waiting_approval"
        assert status["fencing_token"] == 1
        assert status["run_binding"]["engine_version"] == "v2"

        # Duplicate start with same idempotency_key returns durable receipt without re-executing
        replay = await rt_a.start(
            conversation_id=conv_id,
            owner_id="user-1",
            request_payload=payload,
            idempotency_key="start-key-1",
            chat_message_id="msg-1",
            user_message_id="umsg-1",
        )
        assert replay["idempotent_replay"] is True
        assert replay["run_id"] == run_id

        # 3. Reject V1 checkpoint or cross-runtime checkpoint on resume
        with pytest.raises(HTTPException) as cp_v1_exc:
            await rt_a.resume(
                run_id,
                owner_id="user-1",
                checkpoint_data={"schema_version": 1, "engine_version": "v1", "stage": "legacy"},
            )
        assert cp_v1_exc.value.status_code == 409
        assert cp_v1_exc.value.detail["code"] == "v1_checkpoint_rejected"

        with pytest.raises(HTTPException) as cp_cross_exc:
            await rt_a.resume(
                run_id,
                owner_id="user-1",
                checkpoint_data={
                    "schema_version": 2,
                    "engine_type": "deerflow.experimental",
                    "engine_version": "v2",
                },
            )
        assert cp_cross_exc.value.status_code == 409
        assert cp_cross_exc.value.detail["code"] == "cross_runtime_checkpoint_rejected"

        # 4. Resume on Worker B increments fencing_token to 2 and fences out stale Worker A (fencing_token=1)
        rt_b = V2AgentRuntime(repo, worker_id="worker-b", lease_seconds=30)
        resumed_status = await rt_b.resume(run_id, owner_id="user-1", handler=None)
        assert resumed_status["status"] == "running"
        assert resumed_status["fencing_token"] == 2
        assert resumed_status["worker_id"] == "worker-b"

        # Worker A attempting to append event or save checkpoint with stale token=1 MUST fail with 409
        with pytest.raises(FencingTokenMismatchError) as fence_exc:
            await repo.append_event(
                run_id,
                "assistant.delta",
                {"text": "stale output"},
                worker_id="worker-a",
                fencing_token=1,
            )
        assert fence_exc.value.status_code == 409
        assert fence_exc.value.detail["code"] == "fencing_token_mismatch"

        # Worker B with active token=2 succeeds
        ev = await repo.append_event(
            run_id,
            "assistant.delta",
            {"text": "fresh output"},
            worker_id="worker-b",
            fencing_token=2,
        )
        assert ev.sequence >= 3

        # 5. Cancel run idempotently
        cancelled = await rt_b.cancel(run_id, owner_id="user-1", idempotency_key="cancel-1")
        assert cancelled["cancel_requested"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_append_before_publish_monotonic_sequence_and_stream_gap_recovery(tmp_path):
    engine, factory, repo, rt = await make_v2_runtime(tmp_path)
    try:
        conv_id = str(uuid.uuid4())
        status = await rt.start(
            conversation_id=conv_id,
            owner_id="user-1",
            request_payload={"messages": [{"role": "user", "content": "Hello"}]},
        )
        run_id = status["run_id"]
        token = status["fencing_token"]

        observed_in_db_during_emit: list[int] = []

        async def transport_emitter(event_data: dict):
            # Verify event is ALREADY durable in ar_run_events BEFORE transport_emitter runs!
            seq = event_data["runtime_event_sequence"]
            events_in_db = await repo.list_events(run_id, after_sequence=seq - 1)
            assert any(e.sequence == seq for e in events_in_db)
            observed_in_db_during_emit.append(seq)

        wrapped = rt.wrap_event_emitter(
            run_id,
            transport_emitter,
            worker_id=rt.worker_id,
            fencing_token=token,
            message_id="msg-100",
        )
        assert wrapped is not None
        await wrapped({"type": "status", "data": {"description": "Step 1"}})
        await wrapped({"type": "chat:message:delta", "data": {"content": "Step 2"}})
        assert len(observed_in_db_during_emit) == 2
        assert observed_in_db_during_emit[1] == observed_in_db_during_emit[0] + 1

        # Replay stream from after_sequence=0 -> contiguous, no gap
        stream_ok = await rt.stream(run_id, owner_id="user-1", after_sequence=0)
        assert stream_ok["gap_detected"] is False
        assert stream_ok["snapshot_required"] is False
        assert [e.sequence for e in stream_ok["events"]] == [1, 2, 3, 4]

        # Simulate a sequence gap by deleting sequence 2 from ar_run_events
        async with factory() as db:
            await db.execute(
                delete(WeAIRunEvent).where(
                    WeAIRunEvent.run_id == uuid.UUID(run_id),
                    WeAIRunEvent.sequence == 2,
                )
            )
            await db.commit()

        # Stream must detect the gap and attach status snapshot (never silently skip a gap!)
        stream_gap = await rt.stream(run_id, owner_id="user-1", after_sequence=0)
        assert stream_gap["gap_detected"] is True
        assert stream_gap["snapshot_required"] is True
        assert "snapshot" in stream_gap
        assert stream_gap["snapshot"]["run_id"] == run_id

        # Verify bounded tool output stores large payload in ar_agent_artifacts
        await repo.begin_tool_call(
            run_id,
            call_id="call-big",
            tool_name="inspect_document",
            arguments={"document_id": "doc-1"},
            retry_safe=True,
            worker_id=rt.worker_id,
            fencing_token=token,
        )
        big_output = {"text": "Z" * 12_000}
        bounded = await rt.complete_tool_call(
            run_id,
            "call-big",
            big_output,
            tool_name="inspect_document",
            max_chars=2_000,
            worker_id=rt.worker_id,
            fencing_token=token,
        )
        assert isinstance(bounded, dict)
        assert bounded["truncated"] is True
        assert bounded["artifact_id"] is not None
        assert len(bounded["excerpt"]) <= 2_000
    finally:
        await engine.dispose()


def test_request_budgets_95k_preparation_and_100k_wire_json_limits():
    budget_mgr = RequestBudgetManager(
        RequestBudgetLimits(
            max_json_chars=DEFAULT_MAX_JSON_CHARS,
            max_preparation_chars=DEFAULT_MAX_PREPARATION_CHARS,
        )
    )
    assert budget_mgr.limits.max_preparation_chars == 95_000
    assert budget_mgr.limits.max_json_chars == 100_000

    ctx_mgr = ContextManager(request_budget=budget_mgr)
    gateway = ProviderGateway(context_manager=ctx_mgr)

    # 1. Payload with 110,000 chars across older turns + active user turn:
    # ContextManager compacts complete older turns to stay <= 95,000 preparation chars
    # while preserving system prompt and active user turn!
    older_turn_1 = {"role": "user", "content": "Old question 1: " + ("A" * 40_000)}
    older_turn_2 = {"role": "assistant", "content": "Old answer 1: " + ("B" * 40_000)}
    older_turn_3 = {"role": "user", "content": "Old question 2: " + ("C" * 30_000)}
    older_turn_4 = {"role": "assistant", "content": "Old answer 2: " + ("D" * 30_000)}
    active_user_turn = {"role": "user", "content": "Active question that must be preserved!"}

    payload = {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "Required system instructions."},
            older_turn_1,
            older_turn_2,
            older_turn_3,
            older_turn_4,
            active_user_turn,
        ],
    }
    prepared = gateway.prepare(payload, base_url="https://api.openai.com/v1")
    assert prepared.metrics["serialized_chars"] <= DEFAULT_MAX_PREPARATION_CHARS
    assert prepared.metrics["serialized_chars"] <= DEFAULT_MAX_JSON_CHARS
    assert prepared.metrics["serialized_bytes"] >= prepared.metrics["serialized_chars"]
    assert prepared.metrics["estimated_input_tokens"] > 0
    assert prepared.metrics["compacted_messages"] >= 2
    # Active user turn and system instructions are preserved
    assert prepared.payload["messages"][0]["content"] == "Required system instructions."
    assert prepared.payload["messages"][-1]["content"] == "Active question that must be preserved!"

    # 2. Required content alone > 95,000 preparation limit -> fails clearly before provider call
    oversized_active_payload = {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "Required system instructions."},
            {"role": "user", "content": "U" * 96_000},
        ],
    }
    with pytest.raises(HTTPException) as prep_exc:
        gateway.prepare(oversized_active_payload, base_url="https://api.openai.com/v1")
    assert prep_exc.value.status_code == 413
    assert prep_exc.value.detail["code"] == "request_preparation_char_limit_exceeded"
    assert prep_exc.value.detail["preparation_limit_chars"] == 95_000

    # 3. Final serialized wire body > 100,000 characters -> rejected by RequestBudgetManager.validate
    wire_100k_plus = '{"messages":["' + ("X" * 100_010) + '"]}'
    with pytest.raises(RequestBudgetExceeded) as wire_exc:
        budget_mgr.validate(wire_100k_plus)
    assert wire_exc.value.status_code == 413
    assert wire_exc.value.detail["code"] == "request_json_char_limit_exceeded"
    assert wire_exc.value.detail["limit_chars"] == 100_000


@pytest.mark.asyncio
async def test_read_only_tool_retry_policy_and_external_side_effect_quarantine(tmp_path):
    engine, _, repo, rt = await make_v2_runtime(tmp_path)
    try:
        conv_id = str(uuid.uuid4())
        status = await rt.start(
            conversation_id=conv_id,
            owner_id="user-1",
            request_payload={"messages": [{"role": "user", "content": "Inspect and execute"}]},
        )
        run_id = status["run_id"]
        token = status["fencing_token"]

        # 1. Read-only V2 tool (`inspect_document`) retries up to 3 times on transient ConnectionError
        attempts_ro = 0

        async def flaky_inspect_tool(document_id: str):
            nonlocal attempts_ro
            attempts_ro += 1
            if attempts_ro < 3:
                raise ConnectionError("Transient DB pool reset")
            return {"document_id": document_id, "status": "complete"}

        res_ro = await execute_v2_tool_with_retry(
            repo,
            run_id,
            call_id="call-ro-1",
            tool_name="inspect_document",
            arguments={"document_id": "doc-1"},
            tool_fn=flaky_inspect_tool,
            max_retries=3,
            base_delay_seconds=0.001,
            worker_id=rt.worker_id,
            fencing_token=token,
        )
        assert res_ro["status"] == "complete"
        assert attempts_ro == 3

        # 2. Read-only V2 tool NEVER retries on auth (FORBIDDEN) or validation (INVALID_ARGUMENT) errors
        attempts_auth = 0

        async def forbidden_tool(document_id: str):
            nonlocal attempts_auth
            attempts_auth += 1
            raise ToolError("FORBIDDEN", "Access denied to document", status_code=403)

        with pytest.raises(ToolError) as auth_exc:
            await execute_v2_tool_with_retry(
                repo,
                run_id,
                call_id="call-ro-forbidden",
                tool_name="read_spreadsheet_range",
                arguments={"document_id": "doc-secret"},
                tool_fn=forbidden_tool,
                max_retries=3,
                base_delay_seconds=0.001,
                worker_id=rt.worker_id,
                fencing_token=token,
            )
        assert auth_exc.value.code == "FORBIDDEN"
        assert attempts_auth == 1  # Zero retries on auth error!

        # 3. External non-idempotent side-effect tool failing with TimeoutError is QUARANTINED (never retried blindly)
        attempts_ext = 0

        async def external_webhook_tool(target_url: str):
            nonlocal attempts_ext
            attempts_ext += 1
            raise TimeoutError("Upstream webhook timed out after request dispatch")

        with pytest.raises(TimeoutError):
            await execute_v2_tool_with_retry(
                repo,
                run_id,
                call_id="call-ext-1",
                tool_name="mcp_external_webhook_post",
                arguments={"target_url": "https://example.com/hook"},
                tool_fn=external_webhook_tool,
                external_side_effect=True,
                worker_id=rt.worker_id,
                fencing_token=token,
            )
        assert attempts_ext == 1  # Never retried blindly!

        # Verify run is now paused with quarantined tool call, and resume is blocked!
        paused_status = await rt.get_status(run_id, owner_id="user-1")
        assert paused_status["status"] == "paused"
        assert paused_status["quarantined"] is True
        assert paused_status["quarantined_calls"][0]["call_id"] == "call-ext-1"

        with pytest.raises(HTTPException) as resume_blocked_exc:
            await rt.resume(run_id, owner_id="user-1")
        assert resume_blocked_exc.value.status_code == 409
        assert resume_blocked_exc.value.detail["code"] == "quarantine_active"

        # 4. Reconcile the quarantined tool invocation -> unblocks resume!
        await repo.reconcile_tool_invocation(
            run_id,
            "call-ext-1",
            decision="succeeded",
            reconciled_by="admin-operator",
            notes="Verified webhook delivery via downstream audit log",
            result_excerpt='{"delivered": true}',
        )
        reconciled_status = await rt.get_status(run_id, owner_id="user-1")
        assert reconciled_status["quarantined"] is False
        assert reconciled_status["status"] == "interrupted"

        resumed = await rt.resume(run_id, owner_id="user-1", handler=None)
        assert resumed["status"] == "running"
    finally:
        await engine.dispose()
