"""Greenfield V2 AgentRuntime Engine, Retry Policy, and Quarantine Coordinator (Features 30–32).

Implements the canonical runtime contract (`start`, `stream(after_sequence)`, `cancel`,
`resume`, `get_status`) over V2 `ar_*` tables, enforcing:
- Immutable conversation and run bindings (`v2` only; V1 checkpoints rejected)
- Monotonic fencing tokens on `ar_execution_leases`
- Append-before-publish durable events and gap-free stream replay with snapshot recovery
- Read-only V2 tool transient retry (up to 3 retries with exponential backoff; zero retries on auth/validation/permanent errors)
- Quarantine of unknown external side-effect outcomes until explicit reconciliation
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from weai.agent_runtime.outputs import ToolOutputManager
from weai.agent_runtime.request_budget import RequestBudgetManager
from weai.persistence.v2.tools.cursor import CursorValidationError
from weai.persistence.v2.tools.document_tools import DocumentToolError
from weai.persistence.v2.tools.pagination import OutputBudgetTooSmallError
from weai.utils.json_codec import JSONCodec

from .repository import (
    FencingTokenMismatchError,
    V2_ENGINE_TYPE,
    V2_ENGINE_VERSION,
    V2AgentRepository,
    to_uuid,
)

log = logging.getLogger(__name__)

READ_ONLY_V2_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_document",
        "read_document_range",
        "read_spreadsheet_range",
        "search_document",
        "get_document_task",
    }
)

IDEMPOTENT_MUTATION_V2_TOOLS: frozenset[str] = frozenset(
    {
        "execute_document_analysis",
        "start_document_summary",
        "export_document",
        "propose_document_edit",
    }
)

NON_RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "FORBIDDEN",
        "UNAUTHORIZED",
        "DOCUMENT_NOT_FOUND",
        "REVISION_NOT_FOUND",
        "TASK_NOT_FOUND",
        "INVALID_ARGUMENT",
        "UNSUPPORTED_FORMAT",
        "UNSUPPORTED_EDIT_OPERATION",
        "OUTPUT_BUDGET_TOO_SMALL",
        "CURSOR_INVALID",
        "CURSOR_EXPIRED",
        "CURSOR_SCOPE_MISMATCH",
        "CURSOR_BUILD_RETIRED",
        "INDEX_NOT_READY",
        "INDEX_FAILED",
    }
)

TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
     asyncio.TimeoutError,
    OSError,
)


def is_read_only_v2_tool(tool_name: str) -> bool:
    """Returns True if the tool is a read-only V2 document tool safe for automatic retry."""
    return tool_name in READ_ONLY_V2_TOOLS


def is_idempotent_v2_tool(tool_name: str) -> bool:
    """Returns True if the tool is either read-only or uses an internal idempotency ledger."""
    return tool_name in READ_ONLY_V2_TOOLS or tool_name in IDEMPOTENT_MUTATION_V2_TOOLS


def is_non_retryable_tool_error(exc: BaseException) -> bool:
    """Returns True when an error is an auth, validation, cursor, or permanent domain error."""
    if isinstance(exc, (DocumentToolError, CursorValidationError, OutputBudgetTooSmallError, ValueError, TypeError, KeyError, PermissionError)):
        return True
    if isinstance(exc, HTTPException):
        if exc.status_code in (400, 401, 403, 404, 409, 413, 422):
            return True
        if isinstance(exc.detail, dict) and exc.detail.get("code") in NON_RETRYABLE_ERROR_CODES:
            return True
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in NON_RETRYABLE_ERROR_CODES:
        return True
    return False


def is_transient_connection_error(exc: BaseException) -> bool:
    """Returns True only for genuine transient connection/timeout failures."""
    if is_non_retryable_tool_error(exc):
        return False
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    transient_flag = getattr(exc, "transient", False)
    return bool(transient_flag)


class V2AgentRuntime:
    """Production coordinator for AgentRuntime V2 backed by `ar_*` tables."""

    def __init__(
        self,
        repository: V2AgentRepository,
        *,
        output_manager: ToolOutputManager | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ):
        self.repository = repository
        self.engine_type = V2_ENGINE_TYPE
        self.engine_version = V2_ENGINE_VERSION
        self.output_manager = output_manager or ToolOutputManager(10_000)
        self.worker_id = worker_id or f"v2-worker-{uuid.uuid4()}"
        self.lease_seconds = max(5, int(lease_seconds))

    async def start(
        self,
        *,
        conversation_id: str | uuid.UUID,
        owner_id: str,
        request_payload: dict[str, Any],
        idempotency_key: str | None = None,
        chat_message_id: str | None = None,
        user_message_id: str | None = None,
        model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        handler: Callable[[], Awaitable[Any]] | None = None,
    ) -> Any:
        """Starts or idempotently replays a V2 run."""
        meta = metadata if metadata is not None else {}
        effective_idem = idempotency_key or (f"chat:{chat_message_id or user_message_id}" if (chat_message_id or user_message_id) else None)

        run, binding, is_replay = await self.repository.create_run(
            conversation_id,
            owner_id,
            request_payload,
            idempotency_key=effective_idem,
            engine_type=self.engine_type,
            engine_version=self.engine_version,
            chat_message_id=chat_message_id,
            user_message_id=user_message_id,
            model_id=model_id,
        )

        if is_replay and run.status != "pending":
            status = await self.repository.get_run_status(run.id, owner_id=owner_id)
            assert status is not None
            meta["v2_run_id"] = str(run.id)
            meta["agent_turn_id"] = str(run.id)
            meta["agent_run_binding"] = status["run_binding"]
            return {**status, "idempotent_replay": True}

        try:
            claimed_run, lease = await self.repository.claim_run(
                run.id,
                self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail == "Agent run cannot be claimed in its current state.":
                status = await self.repository.get_run_status(run.id, owner_id=owner_id)
                assert status is not None
                return {**status, "idempotent_replay": True}
            raise

        fencing_token = int(lease.fencing_token)
        meta["v2_run_id"] = str(claimed_run.id)
        meta["agent_turn_id"] = str(claimed_run.id)
        meta["v2_worker_id"] = self.worker_id
        meta["v2_fencing_token"] = fencing_token
        meta["agent_turn_attempt"] = fencing_token
        meta["agent_run_binding"] = {
            "engine_type": binding.engine_type,
            "engine_version": binding.engine_version,
            "engine_run_id": str(claimed_run.id),
            "engine_thread_id": str(claimed_run.conversation_id),
            "tool_snapshot_hash": binding.tool_snapshot_hash,
            "skill_snapshot_hash": binding.skill_snapshot_hash,
        }

        cp = await self.repository.save_checkpoint(
            claimed_run.id,
            {
                "stage": "request_prepared",
                "chat_message_id": chat_message_id,
                "user_message_id": user_message_id,
                "request_payload": request_payload,
            },
            memory_frame={"turn": int(claimed_run.current_turn)},
            worker_id=self.worker_id,
            fencing_token=fencing_token,
        )

        if handler is None:
            status = await self.repository.get_run_status(claimed_run.id, owner_id=owner_id)
            assert status is not None
            return status

        return await self._execute_claimed_handler(
            claimed_run.id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            checkpoint_version=cp.version,
            chat_message_id=chat_message_id,
            metadata=meta,
            handler=handler,
        )

    async def _execute_claimed_handler(
        self,
        run_id: uuid.UUID,
        *,
        owner_id: str,
        fencing_token: int,
        checkpoint_version: int,
        chat_message_id: str | None,
        metadata: dict[str, Any],
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        try:
            result = await handler()
            if metadata.get("agent_error"):
                await self.repository.set_run_status(
                    run_id,
                    "failed",
                    event_type="run.failed",
                    payload={"run_id": str(run_id), "error": metadata["agent_error"]},
                    error_json=metadata["agent_error"],
                    worker_id=self.worker_id,
                    fencing_token=fencing_token,
                )
            elif isinstance(result, Response) and result.status_code >= 400:
                err = {"code": "chat_response_error", "status_code": result.status_code}
                await self.repository.set_run_status(
                    run_id,
                    "failed",
                    event_type="run.failed",
                    payload={"run_id": str(run_id), "error": err},
                    error_json=err,
                    worker_id=self.worker_id,
                    fencing_token=fencing_token,
                )
            elif isinstance(result, dict) and result.get("paused"):
                await self.repository.set_run_status(
                    run_id,
                    "waiting_approval",
                    event_type="run.waiting_approval",
                    payload={"run_id": str(run_id), "message_id": chat_message_id},
                    worker_id=self.worker_id,
                    fencing_token=fencing_token,
                )
            elif await self.repository.has_quarantined_tools(run_id):
                # Run was already paused by fail_tool_call(uncertain=True)
                pass
            else:
                status_snap = await self.repository.get_run_status(run_id, owner_id=owner_id)
                if status_snap and status_snap["cancel_requested"]:
                    await self.repository.set_run_status(
                        run_id,
                        "cancelled",
                        event_type="run.cancelled",
                        payload={"run_id": str(run_id)},
                        worker_id=self.worker_id,
                        fencing_token=fencing_token,
                    )
                else:
                    await self.repository.set_run_status(
                        run_id,
                        "completed",
                        event_type="run.completed",
                        payload={
                            "run_id": str(run_id),
                            "message_id": chat_message_id,
                            "checkpoint_version": checkpoint_version,
                        },
                        worker_id=self.worker_id,
                        fencing_token=fencing_token,
                    )
            return result
        except FencingTokenMismatchError:
            raise
        except asyncio.CancelledError:
            has_q = await self.repository.has_quarantined_tools(run_id)
            status_snap = await self.repository.get_run_status(run_id, owner_id=owner_id)
            cancel_req = bool(status_snap and status_snap.get("cancel_requested"))
            target_status = "paused" if has_q else "cancelled" if cancel_req else "interrupted"
            ev_type = (
                "run.recovery_required"
                if has_q
                else "run.cancelled"
                if cancel_req
                else "run.interrupted"
            )
            await self.repository.set_run_status(
                run_id,
                target_status,
                event_type=ev_type,
                payload={"run_id": str(run_id), "reason": "cancelled"},
                worker_id=self.worker_id,
                fencing_token=fencing_token,
            )
            raise
        except BaseException as exc:
            if not await self.repository.has_quarantined_tools(run_id):
                err_payload = {
                    "code": getattr(exc, "code", "run_failed"),
                    "message": str(exc)[:1000] or type(exc).__name__,
                }
                try:
                    await self.repository.set_run_status(
                        run_id,
                        "failed",
                        event_type="run.failed",
                        payload={"run_id": str(run_id), "error": err_payload},
                        error_json=err_payload,
                        worker_id=self.worker_id,
                        fencing_token=fencing_token,
                    )
                except FencingTokenMismatchError:
                    pass
            raise

    async def stream(
        self,
        run_id: str | uuid.UUID,
        *,
        owner_id: str,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Streams durable events after `after_sequence`, attaching status snapshot on any gap."""
        status = await self.repository.get_run_status(run_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")

        page = await self.repository.get_event_page(run_id, after_sequence=after_sequence, limit=limit)
        if page["snapshot_required"]:
            fresh_status = await self.repository.get_run_status(run_id, owner_id=owner_id)
            if fresh_status is None:
                raise HTTPException(status_code=404, detail="Agent run not found.")
            page["snapshot"] = fresh_status
        return page

    async def get_status(self, run_id: str | uuid.UUID, *, owner_id: str) -> dict[str, Any]:
        status = await self.repository.get_run_status(run_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        return status

    async def cancel(
        self,
        run_id: str | uuid.UUID,
        *,
        owner_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        await self.repository.request_cancel_run(
            run_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )
        status = await self.repository.get_run_status(run_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        return status

    async def resume(
        self,
        run_id: str | uuid.UUID,
        *,
        owner_id: str,
        handler: Callable[[], Awaitable[Any]] | None = None,
        checkpoint_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Resumes a paused/waiting_approval/interrupted V2 run after verifying bindings and checkpoints."""
        status = await self.repository.get_run_status(run_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")

        if status["cancel_requested"] or status["status"] in ("completed", "failed", "cancelled"):
            raise HTTPException(status_code=409, detail="Agent run cannot be resumed in its current state.")

        if status["quarantined"] or await self.repository.has_quarantined_tools(run_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "quarantine_active",
                    "message": "An unknown external tool outcome blocks resume until reconciled.",
                },
            )

        run_binding = status["run_binding"]
        if (
            run_binding.get("engine_type") != self.engine_type
            or run_binding.get("engine_version") != self.engine_version
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "runtime_binding_mismatch",
                    "message": "Run is bound to a different execution engine or version.",
                },
            )

        cp_to_validate = checkpoint_data
        if cp_to_validate is None:
            latest_cp = await self.repository.latest_checkpoint(run_id)
            if latest_cp is None:
                raise HTTPException(status_code=409, detail="No durable checkpoint exists to resume this run.")
            cp_to_validate = latest_cp.state_graph_json

        validated_state = await self.repository.validate_checkpoint_for_resume(run_id, cp_to_validate)

        claimed_run, lease = await self.repository.claim_run(
            run_id,
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        fencing_token = int(lease.fencing_token)
        meta = metadata if metadata is not None else {}
        meta["v2_run_id"] = str(claimed_run.id)
        meta["agent_turn_id"] = str(claimed_run.id)
        meta["v2_worker_id"] = self.worker_id
        meta["v2_fencing_token"] = fencing_token
        meta["resumed_from_checkpoint"] = validated_state

        cp = await self.repository.save_checkpoint(
            claimed_run.id,
            {
                "stage": "run_resumed",
                "previous_stage": validated_state.get("stage"),
            },
            memory_frame={"turn": int(claimed_run.current_turn), "resumed": True},
            worker_id=self.worker_id,
            fencing_token=fencing_token,
        )

        if handler is None:
            resumed_status = await self.repository.get_run_status(claimed_run.id, owner_id=owner_id)
            assert resumed_status is not None
            return resumed_status

        return await self._execute_claimed_handler(
            claimed_run.id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            checkpoint_version=cp.version,
            chat_message_id=validated_state.get("chat_message_id"),
            metadata=meta,
            handler=handler,
        )

    def wrap_event_emitter(
        self,
        run_id: str | uuid.UUID,
        emitter: Callable[[dict[str, Any]], Awaitable[Any]] | None,
        *,
        worker_id: str | None = None,
        fencing_token: int | None = None,
        message_id: str | None = None,
    ) -> Callable[[dict[str, Any]], Awaitable[Any]] | None:
        """Wraps a transport emitter so every event is persisted to `ar_run_events` before publish."""
        if emitter is None:
            return None
        r_uuid = to_uuid(run_id, "run")
        eff_worker = worker_id or self.worker_id

        async def durable_emitter(event_data: dict[str, Any]) -> Any:
            event_type = str(event_data.get("type") or "unknown")[:50]
            payload: dict[str, Any] = {
                "message_id": message_id,
                "event": event_data,
            }
            serialized = JSONCodec.dumps(payload, ensure_ascii=False)
            if len(serialized) > 32_000 or len(serialized.encode("utf-8")) > 64_000:
                payload = {
                    "message_id": message_id,
                    "event_type": event_type,
                    "snapshot_required": True,
                    "original_chars": len(serialized),
                    "original_bytes": len(serialized.encode("utf-8")),
                }

            record = await self.repository.append_event(
                r_uuid,
                f"client.{event_type}",
                payload,
                worker_id=eff_worker,
                fencing_token=fencing_token,
            )
            forwarded = copy.deepcopy(event_data)
            if isinstance(forwarded, dict):
                forwarded["runtime_run_id"] = str(r_uuid)
                forwarded["runtime_event_id"] = str(record.id)
                forwarded["runtime_event_sequence"] = int(record.sequence)
            result = await emitter(forwarded)
            await self.repository.mark_event_published(record.id)
            return result

        return durable_emitter

    async def complete_tool_call(
        self,
        run_id: str | uuid.UUID,
        call_id: str,
        result_data: Any,
        *,
        tool_name: str = "tool",
        max_chars: int = 8_000,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> dict[str, Any] | str:
        """Bounds tool output, stores large outputs in `ar_agent_artifacts`, and completes invocation."""
        serialized = (
            result_data
            if isinstance(result_data, str)
            else json.dumps(result_data, ensure_ascii=False, default=str)
        )
        raw_bytes = serialized.encode("utf-8")
        artifact_id: uuid.UUID | None = None
        bounded_output: dict[str, Any] | str = result_data

        if len(serialized) > max_chars:
            excerpt = serialized[: max(256, max_chars - 256)]
            artifact = await self.repository.store_agent_artifact(
                run_id,
                artifact_name=f"{tool_name}:{call_id}.json",
                content_type="application/json",
                raw_bytes=raw_bytes,
                bounded_excerpt=excerpt,
            )
            artifact_id = artifact.id
            bounded_output = {
                "truncated": True,
                "artifact_id": str(artifact.id),
                "storage_key": artifact.storage_key,
                "checksum_sha256": artifact.checksum_sha256,
                "original_chars": len(serialized),
                "original_bytes": len(raw_bytes),
                "excerpt": excerpt,
            }

        await self.repository.complete_tool_call(
            run_id,
            call_id,
            bounded_output,
            artifact_id=artifact_id,
            worker_id=worker_id or self.worker_id,
            fencing_token=fencing_token,
        )
        return bounded_output

    async def execute_tool(
        self,
        run_id: str | uuid.UUID,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_fn: Callable[..., Awaitable[Any]],
        external_side_effect: bool | None = None,
        idempotency_key: str | None = None,
        max_retries: int = 3,
        base_delay_seconds: float = 0.01,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> Any:
        """Executes a tool with V2 retry policy and external side-effect quarantine."""
        return await execute_v2_tool_with_retry(
            self.repository,
            run_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            tool_fn=tool_fn,
            external_side_effect=external_side_effect,
            idempotency_key=idempotency_key,
            max_retries=max_retries,
            base_delay_seconds=base_delay_seconds,
            worker_id=worker_id or self.worker_id,
            fencing_token=fencing_token,
        )


async def execute_v2_tool_with_retry(
    repository: V2AgentRepository,
    run_id: str | uuid.UUID,
    *,
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    tool_fn: Callable[..., Awaitable[Any]],
    external_side_effect: bool | None = None,
    idempotency_key: str | None = None,
    max_retries: int = 3,
    base_delay_seconds: float = 0.01,
    worker_id: str | None = None,
    fencing_token: int | None = None,
) -> Any:
    """Executes a tool call under M3.5 retry and quarantine rules:

    1. Read-only V2 tools (`inspect_document`, `read_document_range`, `read_spreadsheet_range`,
       `search_document`, `get_document_task`) retry up to `max_retries` (default 3) times with
       exponential backoff on transient connection/timeout errors (`ConnectionError`, `TimeoutError`, `OSError`).
    2. Auth (`FORBIDDEN`), validation (`INVALID_ARGUMENT`, `OUTPUT_BUDGET_TOO_SMALL`), cursor,
       and permanent domain errors NEVER retry.
    3. External tools with potential side effects (`external_side_effect=True` or non-V2 mutating tools)
       never retry blindly on ambiguous/timeout failures; instead they quarantine the invocation
       (`status='unknown'`, `quarantined=True`) and pause the run until reconciled.
    """
    read_only = is_read_only_v2_tool(tool_name)
    is_external = external_side_effect if external_side_effect is not None else (not is_idempotent_v2_tool(tool_name))
    retry_safe = read_only and not is_external

    invocation, acquired = await repository.begin_tool_call(
        run_id,
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments,
        idempotency_key=idempotency_key,
        retry_safe=retry_safe,
        worker_id=worker_id,
        fencing_token=fencing_token,
    )
    if not acquired:
        if invocation.status in ("completed", "succeeded") and invocation.result_excerpt is not None:
            try:
                return json.loads(invocation.result_excerpt)
            except ValueError:
                return invocation.result_excerpt
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_tool_invocation",
                "message": "Tool invocation is already running or completed.",
            },
        )

    allowed_retries = max(0, int(max_retries)) if retry_safe else 0
    attempt = 0

    while True:
        attempt += 1
        try:
            result = await tool_fn(**arguments)
            await repository.complete_tool_call(
                run_id,
                call_id,
                result,
                worker_id=worker_id,
                fencing_token=fencing_token,
            )
            return result
        except BaseException as exc:
            if isinstance(exc, FencingTokenMismatchError):
                raise

            # Check if this is a transient error on a read-only V2 tool
            if retry_safe and is_transient_connection_error(exc) and attempt <= allowed_retries:
                delay = max(0.0, float(base_delay_seconds)) * (2 ** (attempt - 1))
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

            # Determine if this failure has an uncertain external side-effect outcome
            uncertain = bool(
                getattr(exc, "uncertain", False)
                or (is_external and is_transient_connection_error(exc))
            )
            error_payload = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(exc),
                "attempts": attempt,
            }
            await repository.fail_tool_call(
                run_id,
                call_id,
                error_payload,
                uncertain=uncertain,
                worker_id=worker_id,
                fencing_token=fencing_token,
            )
            raise
