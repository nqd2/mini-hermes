"""Greenfield V2 AgentRuntime Repository targeting ar_* tables (Feature 30 & 32).

Replatforms AgentRuntime persistence onto:
- ar_conversation_engine_bindings
- ar_run_bindings
- ar_runs
- ar_run_events
- ar_tool_invocations
- ar_checkpoints
- ar_agent_artifacts
- ar_execution_leases
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from weai.agent_runtime.contracts import compute_run_snapshot_versions
from weai.persistence.v2.models.base import utcnow
from weai.persistence.v2.models.runtime import (
    WeAIAgentArtifact,
    WeAICheckpoint,
    WeAIConversationEngineBinding,
    WeAIExecutionLease,
    WeAIRun,
    WeAIRunBinding,
    WeAIRunEvent,
    WeAIToolInvocation,
)

V2_ENGINE_TYPE = "weai.agent_runtime"
V2_ENGINE_VERSION = "v2"


class FencingTokenMismatchError(HTTPException):
    """Raised when a stale worker attempts to mutate state with an outdated fencing token."""

    def __init__(self, detail: str | dict[str, Any] = "Worker lease expired or fencing token superseded."):
        if isinstance(detail, str):
            detail = {"code": "fencing_token_mismatch", "message": detail}
        super().__init__(status_code=409, detail=detail)


def to_uuid(val: str | uuid.UUID | None, prefix: str = "id") -> uuid.UUID:
    """Converts a UUID or string identifier deterministically to uuid.UUID."""
    if isinstance(val, uuid.UUID):
        return val
    if not val:
        return uuid.uuid4()
    s = str(val).strip()
    try:
        return uuid.UUID(s)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"agent-runtime.v2.{prefix}:{s}")


def _ensure_aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _same_json_value(left: Any, right: Any) -> bool:
    """Strict recursive comparison where booleans and numbers are distinct."""
    if type(left) is not type(right):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if isinstance(left, bool) or isinstance(right, bool):
                return False
            return left == right
        return False
    if isinstance(left, dict):
        if set(left.keys()) != set(right.keys()):
            return False
        return all(_same_json_value(left[k], right[k]) for k in left)
    if isinstance(left, list):
        if len(left) != len(right):
            return False
        return all(_same_json_value(a, b) for a, b in zip(left, right))
    return left == right


def canonical_parameters_hash(arguments: dict[str, Any]) -> str:
    """Computes deterministic 64-char SHA-256 hex digest of tool arguments."""
    canonical = json.dumps(
        arguments or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class V2AgentRepository:
    """Durable repository for AgentRuntime V2 over ar_* tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._append_locks: dict[uuid.UUID, asyncio.Lock] = {}

    def _lock_for_run(self, run_uuid: uuid.UUID) -> asyncio.Lock:
        lock = self._append_locks.get(run_uuid)
        if lock is None:
            lock = asyncio.Lock()
            self._append_locks[run_uuid] = lock
        return lock

    async def ensure_conversation_binding(
        self,
        conversation_id: str | uuid.UUID,
        *,
        engine_type: str = V2_ENGINE_TYPE,
        engine_version: str = V2_ENGINE_VERSION,
    ) -> WeAIConversationEngineBinding:
        """Pins a conversation to an immutable engine type and version (default v2)."""
        conv_uuid = to_uuid(conversation_id, "conversation")
        if engine_version == "v1":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "legacy_engine_rejected",
                    "message": "AgentRuntime V2 persistence rejects V1 engine bindings.",
                },
            )

        async with self._session_factory() as db:
            existing = await db.get(WeAIConversationEngineBinding, conv_uuid)
            if existing is not None:
                if existing.engine_type != engine_type or existing.engine_version != engine_version:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "conversation_engine_mismatch",
                            "message": (
                                f"Conversation {conv_uuid} is bound to "
                                f"{existing.engine_type}:{existing.engine_version} and cannot switch to "
                                f"{engine_type}:{engine_version}."
                            ),
                        },
                    )
                return existing

            binding = WeAIConversationEngineBinding(
                conversation_id=conv_uuid,
                engine_type=engine_type,
                engine_version=engine_version,
                bound_at=utcnow(),
            )
            db.add(binding)
            try:
                await db.commit()
                return binding
            except IntegrityError:
                await db.rollback()
                existing = await db.get(WeAIConversationEngineBinding, conv_uuid)
                if existing is not None:
                    if existing.engine_type != engine_type or existing.engine_version != engine_version:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "conversation_engine_mismatch",
                                "message": "Conversation engine binding mismatch.",
                            },
                        )
                    return existing
                raise

    async def create_run(
        self,
        conversation_id: str | uuid.UUID,
        owner_id: str,
        request_payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        engine_type: str = V2_ENGINE_TYPE,
        engine_version: str = V2_ENGINE_VERSION,
        tool_snapshot_hash: str | None = None,
        skill_snapshot_hash: str | None = None,
        chat_message_id: str | None = None,
        user_message_id: str | None = None,
        model_id: str | None = None,
    ) -> tuple[WeAIRun, WeAIRunBinding, bool]:
        """Creates or idempotently returns a V2 run and its immutable run binding.

        Returns (run, run_binding, is_idempotent_replay).
        """
        conv_binding = await self.ensure_conversation_binding(
            conversation_id,
            engine_type=engine_type,
            engine_version=engine_version,
        )
        conv_uuid = conv_binding.conversation_id

        if tool_snapshot_hash is None or skill_snapshot_hash is None:
            computed_tool, computed_skill = compute_run_snapshot_versions(request_payload)
            tool_snapshot_hash = tool_snapshot_hash or computed_tool.removeprefix("sha256:")
            skill_snapshot_hash = skill_snapshot_hash or computed_skill.removeprefix("sha256:")

        envelope_payload = {
            "payload": request_payload,
            "_meta": {
                "idempotency_key": idempotency_key,
                "chat_message_id": chat_message_id,
                "user_message_id": user_message_id,
                "model_id": model_id,
                "cancel_requested": False,
            },
        }

        async with self._session_factory() as db:
            if idempotency_key:
                stmt_existing = (
                    select(WeAIRun)
                    .where(
                        WeAIRun.conversation_id == conv_uuid,
                        WeAIRun.owner_id == owner_id,
                    )
                    .order_by(WeAIRun.created_at.desc())
                )
                res_existing = await db.execute(stmt_existing)
                for candidate in res_existing.scalars().all():
                    cand_meta = (candidate.request_payload or {}).get("_meta") or {}
                    if cand_meta.get("idempotency_key") == idempotency_key:
                        cand_body = (candidate.request_payload or {}).get("payload")
                        if not _same_json_value(cand_body, request_payload):
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code": "idempotency_key_reused",
                                    "message": "Idempotency key was already used with a different request payload.",
                                },
                            )
                        binding = await db.get(WeAIRunBinding, candidate.id)
                        assert binding is not None
                        return candidate, binding, True

            run_id = (
                to_uuid(f"{conv_uuid}:{owner_id}:{idempotency_key}", "run")
                if idempotency_key
                else uuid.uuid4()
            )
            now = utcnow()
            run = WeAIRun(
                id=run_id,
                conversation_id=conv_uuid,
                owner_id=owner_id,
                status="pending",
                current_turn=1,
                next_event_sequence=2,
                request_payload=envelope_payload,
                error_json=None,
                created_at=now,
                updated_at=now,
            )
            binding = WeAIRunBinding(
                run_id=run_id,
                conversation_id=conv_uuid,
                engine_type=conv_binding.engine_type,
                engine_version=conv_binding.engine_version,
                tool_snapshot_hash=tool_snapshot_hash[:64] if tool_snapshot_hash else None,
                skill_snapshot_hash=skill_snapshot_hash[:64] if skill_snapshot_hash else None,
                created_at=now,
            )
            initial_event = WeAIRunEvent(
                id=uuid.uuid4(),
                run_id=run_id,
                sequence=1,
                event_type="run.queued",
                payload_json={
                    "run_id": str(run_id),
                    "conversation_id": str(conv_uuid),
                    "status": "pending",
                    "engine_type": conv_binding.engine_type,
                    "engine_version": conv_binding.engine_version,
                },
                created_at=now,
            )
            db.add(run)
            db.add(binding)
            db.add(initial_event)
            try:
                await db.commit()
                return run, binding, False
            except IntegrityError:
                await db.rollback()
                existing_run = await db.get(WeAIRun, run_id)
                if existing_run is not None:
                    cand_body = (existing_run.request_payload or {}).get("payload")
                    if not _same_json_value(cand_body, request_payload):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "idempotency_key_reused",
                                "message": "Idempotency key was already used with a different request payload.",
                            },
                        )
                    existing_binding = await db.get(WeAIRunBinding, existing_run.id)
                    assert existing_binding is not None
                    return existing_run, existing_binding, True
                raise

    async def bind_run_contract(
        self,
        run_id: str | uuid.UUID,
        *,
        tool_contract_version: str,
        skill_snapshot_version: str,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIRunBinding:
        """Freezes or verifies tool and skill snapshot hashes on WeAIRunBinding."""
        r_uuid = to_uuid(run_id, "run")
        tool_hash = tool_contract_version.removeprefix("sha256:")[:64]
        skill_hash = skill_snapshot_version.removeprefix("sha256:")[:64]

        async with self._session_factory() as db:
            run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
            if run is None:
                raise HTTPException(status_code=404, detail="Agent run not found.")
            await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

            binding = await db.scalar(select(WeAIRunBinding).where(WeAIRunBinding.run_id == r_uuid).with_for_update())
            if binding is None:
                raise HTTPException(status_code=404, detail="Run binding not found.")

            conv = await db.get(WeAIConversationEngineBinding, run.conversation_id)
            if conv is None or conv.engine_type != binding.engine_type or conv.engine_version != binding.engine_version:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "runtime_binding_mismatch",
                        "message": "Run binding does not match conversation engine binding.",
                    },
                )

            if binding.tool_snapshot_hash in (None, "pending") and binding.skill_snapshot_hash in (None, "pending"):
                binding.tool_snapshot_hash = tool_hash
                binding.skill_snapshot_hash = skill_hash
                await db.commit()
                return binding

            if binding.tool_snapshot_hash != tool_hash or binding.skill_snapshot_hash != skill_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "runtime_snapshot_mismatch",
                        "message": "Run contract snapshot hashes cannot change mid-run or across resume.",
                    },
                )
            return binding

    async def _verify_fencing(
        self,
        db: AsyncSession,
        run: WeAIRun,
        *,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIExecutionLease | None:
        if worker_id is None and fencing_token is None:
            return await db.get(WeAIExecutionLease, run.id)

        lease = await db.get(WeAIExecutionLease, run.id)
        if lease is None:
            raise FencingTokenMismatchError("No active execution lease exists for this run.")
        if fencing_token is not None and lease.fencing_token != int(fencing_token):
            raise FencingTokenMismatchError(
                {
                    "code": "fencing_token_mismatch",
                    "message": f"Stale fencing token {fencing_token} (current is {lease.fencing_token}).",
                    "provided_token": fencing_token,
                    "active_token": lease.fencing_token,
                }
            )
        if worker_id is not None and lease.worker_id != worker_id:
            raise FencingTokenMismatchError(
                {
                    "code": "worker_ownership_mismatch",
                    "message": f"Run lease is owned by '{lease.worker_id}', not '{worker_id}'.",
                }
            )
        now = utcnow()
        exp = _ensure_aware(lease.expires_at)
        if exp is not None and exp <= now:
            raise FencingTokenMismatchError(
                {
                    "code": "lease_expired",
                    "message": "Execution lease has expired.",
                }
            )
        if run.status != "running":
            raise FencingTokenMismatchError(
                {
                    "code": "run_not_running",
                    "message": f"Run is in state '{run.status}', not 'running'.",
                }
            )
        return lease

    async def has_quarantined_tools(self, run_id: str | uuid.UUID) -> bool:
        r_uuid = to_uuid(run_id, "run")
        async with self._session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(WeAIToolInvocation)
                .where(
                    WeAIToolInvocation.run_id == r_uuid,
                    or_quarantined := (WeAIToolInvocation.quarantined.is_(True) | (WeAIToolInvocation.status == "unknown")),
                )
            )
            return bool(count and count > 0)

    async def claim_run(
        self,
        run_id: str | uuid.UUID,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> tuple[WeAIRun, WeAIExecutionLease]:
        """Acquires or re-acquires execution lease with monotonically increasing fencing_token."""
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")

                conv = await db.get(WeAIConversationEngineBinding, run.conversation_id)
                binding = await db.get(WeAIRunBinding, run.id)
                if (
                    conv is None
                    or binding is None
                    or conv.engine_type != binding.engine_type
                    or conv.engine_version != binding.engine_version
                    or binding.engine_version != V2_ENGINE_VERSION
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "runtime_binding_mismatch",
                            "message": "Run binding or engine version mismatch prevents claiming run.",
                        },
                    )

                quarantined_count = await db.scalar(
                    select(func.count())
                    .select_from(WeAIToolInvocation)
                    .where(
                        WeAIToolInvocation.run_id == r_uuid,
                        (WeAIToolInvocation.quarantined.is_(True)) | (WeAIToolInvocation.status == "unknown"),
                    )
                )
                if quarantined_count and quarantined_count > 0:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "quarantine_active",
                            "message": "An unknown external tool outcome is quarantined and blocks execution until reconciled.",
                        },
                    )

                now = utcnow()
                lease = await db.scalar(
                    select(WeAIExecutionLease).where(WeAIExecutionLease.run_id == r_uuid).with_for_update()
                )

                if run.status == "running" and lease is not None:
                    exp = _ensure_aware(lease.expires_at)
                    if exp is not None and exp > now and lease.worker_id != worker_id:
                        raise HTTPException(
                            status_code=409,
                            detail="Agent run cannot be claimed in its current state.",
                        )

                if run.status not in ("pending", "waiting_approval", "interrupted", "paused", "running"):
                    raise HTTPException(
                        status_code=409,
                        detail="Agent run cannot be claimed in its current state.",
                    )

                expires = now + dt.timedelta(seconds=max(1, int(lease_seconds)))
                if lease is None:
                    lease = WeAIExecutionLease(
                        run_id=r_uuid,
                        worker_id=worker_id,
                        fencing_token=1,
                        heartbeat_at=now,
                        expires_at=expires,
                    )
                    db.add(lease)
                else:
                    lease.fencing_token = int(lease.fencing_token) + 1
                    lease.worker_id = worker_id
                    lease.heartbeat_at = now
                    lease.expires_at = expires
                    run.current_turn = int(run.current_turn) + 1

                run.status = "running"
                run.updated_at = now
                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1

                started_event = WeAIRunEvent(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    sequence=seq,
                    event_type="run.started",
                    payload_json={
                        "run_id": str(r_uuid),
                        "worker_id": worker_id,
                        "fencing_token": int(lease.fencing_token),
                    },
                    created_at=now,
                )
                db.add(started_event)
                await db.commit()
                return run, lease

    async def heartbeat_lease(
        self,
        run_id: str | uuid.UUID,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int = 60,
    ) -> WeAIExecutionLease:
        """Renews worker lease heartbeat if fencing_token and worker_id still match."""
        r_uuid = to_uuid(run_id, "run")
        async with self._session_factory() as db:
            run = await db.get(WeAIRun, r_uuid)
            if run is None:
                raise HTTPException(status_code=404, detail="Agent run not found.")
            lease = await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)
            assert lease is not None
            now = utcnow()
            lease.heartbeat_at = now
            lease.expires_at = now + dt.timedelta(seconds=max(1, int(lease_seconds)))
            await db.commit()
            return lease

    async def append_event(
        self,
        run_id: str | uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        *,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIRunEvent:
        """Appends a monotonically sequenced durable event to ar_run_events before publish."""
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1
                now = utcnow()
                run.updated_at = now

                event_record = WeAIRunEvent(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    sequence=seq,
                    event_type=str(event_type)[:64],
                    payload_json=dict(payload or {}),
                    created_at=now,
                )
                db.add(event_record)
                await db.commit()
                return event_record

    async def mark_event_published(self, event_id: str | uuid.UUID) -> None:
        e_uuid = to_uuid(event_id, "event")
        async with self._session_factory() as db:
            ev = await db.get(WeAIRunEvent, e_uuid)
            if ev is not None:
                updated_payload = dict(ev.payload_json or {})
                updated_payload["_published_at"] = utcnow().isoformat()
                ev.payload_json = updated_payload
                await db.commit()

    async def list_events(
        self,
        run_id: str | uuid.UUID,
        *,
        after_sequence: int = 0,
    ) -> list[WeAIRunEvent]:
        r_uuid = to_uuid(run_id, "run")
        async with self._session_factory() as db:
            stmt = (
                select(WeAIRunEvent)
                .where(WeAIRunEvent.run_id == r_uuid, WeAIRunEvent.sequence > int(after_sequence))
                .order_by(WeAIRunEvent.sequence.asc())
            )
            res = await db.execute(stmt)
            return list(res.scalars().all())

    async def get_event_page(
        self,
        run_id: str | uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Reads a watermark-bounded replay page and detects any sequence gaps."""
        r_uuid = to_uuid(run_id, "run")
        cursor = max(0, int(after_sequence))
        page_limit = max(1, min(int(limit), 1000))

        async with self._session_factory() as db:
            run = await db.get(WeAIRun, r_uuid)
            if run is None:
                raise HTTPException(status_code=404, detail="Agent run not found.")

            latest_sequence = max(0, int(run.next_event_sequence) - 1)
            stmt = (
                select(WeAIRunEvent)
                .where(
                    WeAIRunEvent.run_id == r_uuid,
                    WeAIRunEvent.sequence > cursor,
                    WeAIRunEvent.sequence <= latest_sequence,
                )
                .order_by(WeAIRunEvent.sequence.asc())
                .limit(page_limit + 1)
            )
            rows = list((await db.scalars(stmt)).all())

        has_more = len(rows) > page_limit
        events = rows[:page_limit]

        gap_detected = False
        expected = cursor + 1
        if not events:
            if cursor < latest_sequence:
                gap_detected = True
                has_more = True
        else:
            for ev in events:
                if ev.sequence != expected:
                    gap_detected = True
                expected = ev.sequence + 1

        next_after_sequence = events[-1].sequence if events else cursor
        if not has_more and events and events[-1].sequence < latest_sequence:
            has_more = True

        oversized_marker = any(
            isinstance(ev.payload_json, dict) and ev.payload_json.get("snapshot_required") is True
            for ev in events
        )
        snapshot_required = gap_detected or oversized_marker

        return {
            "run_id": str(r_uuid),
            "events": events,
            "after_sequence": cursor,
            "next_after_sequence": next_after_sequence,
            "latest_sequence": latest_sequence,
            "has_more": has_more,
            "gap_detected": gap_detected,
            "snapshot_required": snapshot_required,
        }

    async def save_checkpoint(
        self,
        run_id: str | uuid.UUID,
        state_graph: dict[str, Any],
        memory_frame: dict[str, Any] | None = None,
        *,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAICheckpoint:
        """Saves a monotonically versioned V2 checkpoint in ar_checkpoints."""
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)
                binding = await db.get(WeAIRunBinding, r_uuid)

                max_ver = await db.scalar(
                    select(func.max(WeAICheckpoint.version)).where(WeAICheckpoint.run_id == r_uuid)
                )
                next_ver = int(max_ver or 0) + 1

                enriched_state = {
                    "schema_version": 2,
                    "engine_type": binding.engine_type if binding else V2_ENGINE_TYPE,
                    "engine_version": binding.engine_version if binding else V2_ENGINE_VERSION,
                    "run_id": str(r_uuid),
                    "conversation_id": str(run.conversation_id),
                    "tool_snapshot_hash": binding.tool_snapshot_hash if binding else None,
                    "skill_snapshot_hash": binding.skill_snapshot_hash if binding else None,
                    **dict(state_graph or {}),
                }
                cp = WeAICheckpoint(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    version=next_ver,
                    state_graph_json=enriched_state,
                    memory_frame_json=dict(memory_frame or {}),
                    created_at=utcnow(),
                )
                db.add(cp)
                await db.commit()
                return cp

    async def latest_checkpoint(self, run_id: str | uuid.UUID) -> WeAICheckpoint | None:
        r_uuid = to_uuid(run_id, "run")
        async with self._session_factory() as db:
            stmt = (
                select(WeAICheckpoint)
                .where(WeAICheckpoint.run_id == r_uuid)
                .order_by(WeAICheckpoint.version.desc())
                .limit(1)
            )
            res = await db.execute(stmt)
            return res.scalar_one_or_none()

    async def validate_checkpoint_for_resume(
        self,
        run_id: str | uuid.UUID,
        checkpoint_data: dict[str, Any] | WeAICheckpoint,
    ) -> dict[str, Any]:
        """Verifies a checkpoint belongs to this V2 run and rejects V1 or cross-runtime checkpoints."""
        r_uuid = to_uuid(run_id, "run")
        state = (
            checkpoint_data.state_graph_json
            if isinstance(checkpoint_data, WeAICheckpoint)
            else dict(checkpoint_data or {})
        )

        schema_ver = state.get("schema_version")
        engine_ver = state.get("engine_version") or (state.get("run_binding") or {}).get("engine_version")
        engine_type = state.get("engine_type") or (state.get("run_binding") or {}).get("engine_type")

        if schema_ver == 1 or engine_ver == "v1" or engine_ver != V2_ENGINE_VERSION:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "v1_checkpoint_rejected",
                    "message": "Cannot resume a V1 or mismatched engine version checkpoint on V2 runtime.",
                },
            )
        if engine_type and engine_type != V2_ENGINE_TYPE:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cross_runtime_checkpoint_rejected",
                    "message": f"Cannot resume checkpoint from engine '{engine_type}' on '{V2_ENGINE_TYPE}'.",
                },
            )

        async with self._session_factory() as db:
            run = await db.get(WeAIRun, r_uuid)
            binding = await db.get(WeAIRunBinding, r_uuid)
            if run is None or binding is None:
                raise HTTPException(status_code=404, detail="Agent run not found.")

            cp_run_id = state.get("run_id") or (state.get("run_binding") or {}).get("engine_run_id")
            if cp_run_id and str(cp_run_id) != str(r_uuid):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "checkpoint_run_mismatch",
                        "message": "Checkpoint belongs to a different run_id.",
                    },
                )
            cp_tool_hash = state.get("tool_snapshot_hash")
            if cp_tool_hash and binding.tool_snapshot_hash and cp_tool_hash != binding.tool_snapshot_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "runtime_snapshot_mismatch",
                        "message": "Checkpoint tool snapshot hash does not match frozen run binding.",
                    },
                )
            cp_skill_hash = state.get("skill_snapshot_hash")
            if cp_skill_hash and binding.skill_snapshot_hash and cp_skill_hash != binding.skill_snapshot_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "runtime_snapshot_mismatch",
                        "message": "Checkpoint skill snapshot hash does not match frozen run binding.",
                    },
                )
        return state

    async def begin_tool_call(
        self,
        run_id: str | uuid.UUID,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str | None = None,
        retry_safe: bool = False,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> tuple[WeAIToolInvocation, bool]:
        """Records or deduplicates a tool invocation in ar_tool_invocations."""
        r_uuid = to_uuid(run_id, "run")
        idem_key = idempotency_key or f"{r_uuid}:{call_id}"
        param_hash = canonical_parameters_hash(arguments)

        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

                stmt_existing = select(WeAIToolInvocation).where(
                    WeAIToolInvocation.run_id == r_uuid,
                    (WeAIToolInvocation.call_id == call_id) | (WeAIToolInvocation.idempotency_key == idem_key),
                ).with_for_update()
                existing = await db.scalar(stmt_existing)

                stored_args = {
                    "arguments": arguments,
                    "_meta": {"retry_safe": bool(retry_safe)},
                }

                if existing is not None:
                    existing_args = (existing.arguments_json or {}).get("arguments", existing.arguments_json)
                    if existing.tool_name != tool_name or not _same_json_value(existing_args, arguments):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "tool_idempotency_conflict",
                                "message": "Tool call_id or idempotency_key reused with different tool or arguments.",
                            },
                        )
                    if existing.quarantined or existing.status == "unknown":
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "EXTERNAL_OUTCOME_UNKNOWN",
                                "message": "Tool invocation outcome is unknown and quarantined for reconciliation.",
                            },
                        )
                    if existing.status in ("completed", "succeeded", "running"):
                        return existing, False
                    if existing.status == "pending" and retry_safe:
                        existing.status = "running"
                        await db.commit()
                        return existing, True
                    return existing, False

                now = utcnow()
                invocation = WeAIToolInvocation(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    call_id=str(call_id),
                    tool_name=str(tool_name),
                    idempotency_key=str(idem_key),
                    parameters_hash=param_hash,
                    arguments_json=stored_args,
                    status="running",
                    quarantined=False,
                    quarantine_reason=None,
                    result_excerpt=None,
                    artifact_id=None,
                    created_at=now,
                )
                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1
                ev = WeAIRunEvent(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    sequence=seq,
                    event_type="tool.started",
                    payload_json={
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "idempotency_key": idem_key,
                        "retry_safe": bool(retry_safe),
                    },
                    created_at=now,
                )
                db.add(invocation)
                db.add(ev)
                await db.commit()
                return invocation, True

    async def complete_tool_call(
        self,
        run_id: str | uuid.UUID,
        call_id: str,
        result_data: Any,
        *,
        artifact_id: str | uuid.UUID | None = None,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIToolInvocation:
        """Marks a tool invocation completed and appends tool.completed event."""
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

                inv = await db.scalar(
                    select(WeAIToolInvocation)
                    .where(WeAIToolInvocation.run_id == r_uuid, WeAIToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if inv is None:
                    raise HTTPException(status_code=404, detail="Tool invocation not found.")

                now = utcnow()
                excerpt = (
                    result_data
                    if isinstance(result_data, str)
                    else json.dumps(result_data, ensure_ascii=False, default=str)
                )
                inv.status = "completed"
                inv.result_excerpt = excerpt[:8192]
                inv.artifact_id = to_uuid(artifact_id, "artifact") if artifact_id else None
                inv.completed_at = now

                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1
                ev = WeAIRunEvent(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    sequence=seq,
                    event_type="tool.completed",
                    payload_json={
                        "call_id": call_id,
                        "tool_name": inv.tool_name,
                        "artifact_id": str(inv.artifact_id) if inv.artifact_id else None,
                    },
                    created_at=now,
                )
                db.add(ev)
                await db.commit()
                return inv

    async def fail_tool_call(
        self,
        run_id: str | uuid.UUID,
        call_id: str,
        error: dict[str, Any] | str,
        *,
        uncertain: bool = False,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIToolInvocation:
        """Marks a tool invocation failed, or quarantines it if external outcome is uncertain."""
        r_uuid = to_uuid(run_id, "run")
        err_dict = {"message": error} if isinstance(error, str) else dict(error or {})

        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

                inv = await db.scalar(
                    select(WeAIToolInvocation)
                    .where(WeAIToolInvocation.run_id == r_uuid, WeAIToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if inv is None:
                    raise HTTPException(status_code=404, detail="Tool invocation not found.")

                now = utcnow()
                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1

                if uncertain:
                    inv.status = "unknown"
                    inv.quarantined = True
                    inv.quarantine_reason = str(err_dict.get("message") or "Ambiguous external side-effect outcome")
                    run.status = "paused"
                    run.error_json = {
                        "code": "EXTERNAL_OUTCOME_UNKNOWN",
                        "call_id": call_id,
                        "tool_name": inv.tool_name,
                        **err_dict,
                    }
                    lease = await db.get(WeAIExecutionLease, r_uuid)
                    if lease is not None:
                        lease.expires_at = now
                    ev = WeAIRunEvent(
                        id=uuid.uuid4(),
                        run_id=r_uuid,
                        sequence=seq,
                        event_type="tool.outcome_unknown",
                        payload_json={
                            "call_id": call_id,
                            "tool_name": inv.tool_name,
                            "quarantined": True,
                            "error": err_dict,
                        },
                        created_at=now,
                    )
                else:
                    inv.status = "failed"
                    inv.completed_at = now
                    inv.result_excerpt = json.dumps(err_dict, ensure_ascii=False)
                    ev = WeAIRunEvent(
                        id=uuid.uuid4(),
                        run_id=r_uuid,
                        sequence=seq,
                        event_type="tool.failed",
                        payload_json={
                            "call_id": call_id,
                            "tool_name": inv.tool_name,
                            "error": err_dict,
                        },
                        created_at=now,
                    )

                run.updated_at = now
                db.add(ev)
                await db.commit()
                return inv

    async def reconcile_tool_invocation(
        self,
        run_id: str | uuid.UUID,
        call_id: str,
        *,
        decision: str,
        reconciled_by: str,
        notes: str | None = None,
        result_excerpt: str | None = None,
    ) -> WeAIToolInvocation:
        """Clears quarantine on an unknown tool invocation after operator/system reconciliation."""
        if decision not in ("completed", "succeeded", "failed", "cancelled"):
            raise ValueError(f"Invalid reconciliation decision: {decision}")

        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")

                inv = await db.scalar(
                    select(WeAIToolInvocation)
                    .where(WeAIToolInvocation.run_id == r_uuid, WeAIToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if inv is None:
                    raise HTTPException(status_code=404, detail="Tool invocation not found.")

                now = utcnow()
                inv.status = "completed" if decision in ("completed", "succeeded") else decision
                inv.quarantined = False
                inv.quarantine_reason = f"Reconciled as {decision} by {reconciled_by}: {notes or ''}".strip()
                if result_excerpt is not None:
                    inv.result_excerpt = result_excerpt
                inv.completed_at = now

                remaining = await db.scalar(
                    select(func.count())
                    .select_from(WeAIToolInvocation)
                    .where(
                        WeAIToolInvocation.run_id == r_uuid,
                        WeAIToolInvocation.id != inv.id,
                        (WeAIToolInvocation.quarantined.is_(True)) | (WeAIToolInvocation.status == "unknown"),
                    )
                )
                if not remaining and run.status == "paused":
                    run.status = "interrupted"
                    run.error_json = None

                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1
                ev = WeAIRunEvent(
                    id=uuid.uuid4(),
                    run_id=r_uuid,
                    sequence=seq,
                    event_type="tool.reconciled",
                    payload_json={
                        "call_id": call_id,
                        "tool_name": inv.tool_name,
                        "decision": decision,
                        "reconciled_by": reconciled_by,
                        "notes": notes,
                    },
                    created_at=now,
                )
                run.updated_at = now
                db.add(ev)
                await db.commit()
                return inv

    async def store_agent_artifact(
        self,
        run_id: str | uuid.UUID,
        *,
        artifact_name: str,
        content_type: str,
        raw_bytes: bytes,
        bounded_excerpt: str,
        storage_key: str | None = None,
    ) -> WeAIAgentArtifact:
        """Persists large tool output metadata in ar_agent_artifacts."""
        r_uuid = to_uuid(run_id, "run")
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
        art_id = uuid.uuid4()
        async with self._session_factory() as db:
            artifact = WeAIAgentArtifact(
                id=art_id,
                run_id=r_uuid,
                artifact_name=artifact_name[:255],
                content_type=content_type[:128],
                byte_size=len(raw_bytes),
                checksum_sha256=sha256_hex,
                storage_key=storage_key or f"v2/agent-artifacts/{r_uuid}/{art_id}",
                bounded_excerpt=bounded_excerpt,
                created_at=utcnow(),
            )
            db.add(artifact)
            await db.commit()
            return artifact

    async def set_run_status(
        self,
        run_id: str | uuid.UUID,
        status: str,
        *,
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
        error_json: dict[str, Any] | None = None,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> WeAIRun:
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid).with_for_update())
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")
                await self._verify_fencing(db, run, worker_id=worker_id, fencing_token=fencing_token)

                now = utcnow()
                run.status = status
                run.updated_at = now
                if error_json is not None:
                    run.error_json = error_json
                if status in ("completed", "failed", "cancelled"):
                    run.completed_at = now
                    lease = await db.get(WeAIExecutionLease, r_uuid)
                    if lease is not None:
                        lease.expires_at = now

                if event_type:
                    seq = int(run.next_event_sequence)
                    run.next_event_sequence = seq + 1
                    ev = WeAIRunEvent(
                        id=uuid.uuid4(),
                        run_id=r_uuid,
                        sequence=seq,
                        event_type=event_type,
                        payload_json=dict(payload or {"run_id": str(r_uuid), "status": status}),
                        created_at=now,
                    )
                    db.add(ev)

                await db.commit()
                return run

    async def request_cancel_run(
        self,
        run_id: str | uuid.UUID,
        *,
        owner_id: str,
        idempotency_key: str | None = None,
    ) -> WeAIRun:
        """Idempotently requests or completes run cancellation while preserving unknown tool outcomes."""
        r_uuid = to_uuid(run_id, "run")
        async with self._lock_for_run(r_uuid):
            async with self._session_factory() as db:
                run = await db.scalar(
                    select(WeAIRun).where(WeAIRun.id == r_uuid, WeAIRun.owner_id == owner_id).with_for_update()
                )
                if run is None:
                    raise HTTPException(status_code=404, detail="Agent run not found.")

                req_payload = dict(run.request_payload or {})
                meta = dict(req_payload.get("_meta") or {})
                already_requested = bool(meta.get("cancel_requested"))
                meta["cancel_requested"] = True
                if idempotency_key:
                    meta["cancel_idempotency_key"] = idempotency_key
                req_payload["_meta"] = meta
                run.request_payload = req_payload

                now = utcnow()
                if not already_requested:
                    seq = int(run.next_event_sequence)
                    run.next_event_sequence = seq + 1
                    db.add(
                        WeAIRunEvent(
                            id=uuid.uuid4(),
                            run_id=r_uuid,
                            sequence=seq,
                            event_type="run.cancel_requested",
                            payload_json={"run_id": str(r_uuid), "owner_id": owner_id},
                            created_at=now,
                        )
                    )

                # Check if any tool invocation has unknown outcome (never discard unknown external outcomes!)
                quarantined = await db.scalar(
                    select(func.count())
                    .select_from(WeAIToolInvocation)
                    .where(
                        WeAIToolInvocation.run_id == r_uuid,
                        (WeAIToolInvocation.quarantined.is_(True)) | (WeAIToolInvocation.status == "unknown"),
                    )
                )
                if quarantined and quarantined > 0:
                    run.status = "paused"
                elif run.status in ("pending", "waiting_approval", "interrupted"):
                    run.status = "cancelled"
                    run.completed_at = now
                    seq2 = int(run.next_event_sequence)
                    run.next_event_sequence = seq2 + 1
                    db.add(
                        WeAIRunEvent(
                            id=uuid.uuid4(),
                            run_id=r_uuid,
                            sequence=seq2,
                            event_type="run.cancelled",
                            payload_json={"run_id": str(r_uuid)},
                            created_at=now,
                        )
                    )

                run.updated_at = now
                await db.commit()
                return run

    async def recover_expired_runs(self, *, now: dt.datetime | None = None) -> int:
        """Recovers runs with expired worker leases; quarantines non-idempotent in-flight tools."""
        current_time = _ensure_aware(now) or utcnow()
        recovered_count = 0

        async with self._session_factory() as db:
            stmt = (
                select(WeAIRun, WeAIExecutionLease)
                .join(WeAIExecutionLease, WeAIRun.id == WeAIExecutionLease.run_id)
                .where(WeAIRun.status == "running", WeAIExecutionLease.expires_at <= current_time)
                .with_for_update()
            )
            rows = (await db.execute(stmt)).all()
            for run, lease in rows:
                inv_stmt = (
                    select(WeAIToolInvocation)
                    .where(WeAIToolInvocation.run_id == run.id, WeAIToolInvocation.status == "running")
                    .with_for_update()
                )
                running_invs = list((await db.scalars(inv_stmt)).all())
                has_unsafe = False
                for inv in running_invs:
                    retry_safe = bool(((inv.arguments_json or {}).get("_meta") or {}).get("retry_safe", False))
                    if retry_safe:
                        inv.status = "pending"
                    else:
                        has_unsafe = True
                        inv.status = "unknown"
                        inv.quarantined = True
                        inv.quarantine_reason = "Worker lease expired during non-idempotent external tool execution"

                seq = int(run.next_event_sequence)
                run.next_event_sequence = seq + 1
                if has_unsafe:
                    run.status = "paused"
                    run.error_json = {
                        "code": "EXTERNAL_OUTCOME_UNKNOWN",
                        "message": "Worker lease expired while external tool side effect was in flight.",
                    }
                    ev_type = "run.recovery_required"
                else:
                    run.status = "interrupted"
                    ev_type = "run.interrupted"

                run.updated_at = current_time
                db.add(
                    WeAIRunEvent(
                        id=uuid.uuid4(),
                        run_id=run.id,
                        sequence=seq,
                        event_type=ev_type,
                        payload_json={"run_id": str(run.id), "status": run.status},
                        created_at=current_time,
                    )
                )
                recovered_count += 1

            if recovered_count:
                await db.commit()
        return recovered_count

    async def get_run_status(self, run_id: str | uuid.UUID, *, owner_id: str) -> dict[str, Any] | None:
        """Returns owner-scoped status snapshot with latest sequence, binding, and quarantine flags."""
        r_uuid = to_uuid(run_id, "run")
        async with self._session_factory() as db:
            run = await db.scalar(select(WeAIRun).where(WeAIRun.id == r_uuid, WeAIRun.owner_id == owner_id))
            if run is None:
                return None
            binding = await db.get(WeAIRunBinding, r_uuid)
            lease = await db.get(WeAIExecutionLease, r_uuid)
            cp = await db.scalar(
                select(WeAICheckpoint)
                .where(WeAICheckpoint.run_id == r_uuid)
                .order_by(WeAICheckpoint.version.desc())
                .limit(1)
            )
            quarantined_tools = list(
                (
                    await db.scalars(
                        select(WeAIToolInvocation).where(
                            WeAIToolInvocation.run_id == r_uuid,
                            (WeAIToolInvocation.quarantined.is_(True)) | (WeAIToolInvocation.status == "unknown"),
                        )
                    )
                ).all()
            )
            meta = (run.request_payload or {}).get("_meta") or {}

            return {
                "run_id": str(run.id),
                "turn_id": str(run.id),
                "conversation_id": str(run.conversation_id),
                "owner_id": run.owner_id,
                "status": run.status,
                "current_turn": int(run.current_turn),
                "latest_sequence": max(0, int(run.next_event_sequence) - 1),
                "cancel_requested": bool(meta.get("cancel_requested", False)),
                "fencing_token": int(lease.fencing_token) if lease else 0,
                "worker_id": lease.worker_id if lease else None,
                "latest_checkpoint_version": int(cp.version) if cp else None,
                "quarantined": len(quarantined_tools) > 0,
                "quarantined_calls": [
                    {
                        "call_id": qt.call_id,
                        "tool_name": qt.tool_name,
                        "reason": qt.quarantine_reason,
                    }
                    for qt in quarantined_tools
                ],
                "run_binding": {
                    "engine_type": binding.engine_type if binding else V2_ENGINE_TYPE,
                    "engine_version": binding.engine_version if binding else V2_ENGINE_VERSION,
                    "engine_run_id": str(run.id),
                    "engine_thread_id": str(run.conversation_id),
                    "tool_snapshot_hash": binding.tool_snapshot_hash if binding else None,
                    "skill_snapshot_hash": binding.skill_snapshot_hash if binding else None,
                },
            }
