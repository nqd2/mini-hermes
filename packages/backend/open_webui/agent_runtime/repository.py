"""Transactional persistence and recovery for agent sessions and turns."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    AgentArtifact,
    AgentCheckpoint,
    AgentEvent,
    AgentSession,
    AgentToolInvocation,
    AgentTurn,
    utcnow,
)
from .contracts import ConversationEngineBinding, RunBinding


class AgentRepository:
    """Store durable agent state. Every public mutation commits before returning."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def create_session(
        self,
        owner_id: str,
        chat_id: str | None = None,
        *,
        parent_session_id: str | None = None,
        task_state: dict | None = None,
        engine_binding: ConversationEngineBinding | None = None,
    ) -> AgentSession:
        if chat_id is not None:
            async with self._session_factory() as db:
                existing = await db.scalar(
                    select(AgentSession).where(AgentSession.owner_id == owner_id, AgentSession.chat_id == chat_id)
                )
                if existing:
                    return existing

        session = AgentSession(
            id=str(uuid4()),
            owner_id=owner_id,
            chat_id=chat_id,
            parent_session_id=parent_session_id,
            engine_type=(engine_binding or ConversationEngineBinding()).engine_type,
            engine_version=(engine_binding or ConversationEngineBinding()).engine_version,
            status='active',
            next_turn_sequence=1,
            task_state=task_state,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        async with self._session_factory() as db:
            db.add(session)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if chat_id is None:
                    raise
                existing = await db.scalar(
                    select(AgentSession).where(AgentSession.owner_id == owner_id, AgentSession.chat_id == chat_id)
                )
                if existing:
                    return existing
                raise
            await db.refresh(session)
        return session

    async def get_session(self, session_id: str, *, owner_id: str) -> AgentSession | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentSession).where(AgentSession.id == session_id, AgentSession.owner_id == owner_id)
            )

    async def get_session_for_chat(self, chat_id: str, *, owner_id: str) -> AgentSession | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentSession).where(AgentSession.chat_id == chat_id, AgentSession.owner_id == owner_id)
            )

    async def get_turn_session(self, session_id: str) -> AgentSession | None:
        async with self._session_factory() as db:
            return await db.get(AgentSession, session_id)

    async def update_session_task_state(self, session_id: str, *, owner_id: str, task_state: dict) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                update(AgentSession)
                .where(AgentSession.id == session_id, AgentSession.owner_id == owner_id)
                .values(task_state=task_state, updated_at=utcnow())
            )
            await db.commit()
            return bool(result.rowcount)

    async def update_session_turn_status(
        self,
        session_id: str,
        *,
        owner_id: str,
        turn_id: str,
        status: str,
        objective: str | None = None,
        user_message_id: str | None = None,
    ) -> bool:
        async with self._session_factory() as db:
            async with db.begin():
                session = await db.scalar(
                    select(AgentSession)
                    .where(AgentSession.id == session_id, AgentSession.owner_id == owner_id)
                    .with_for_update()
                )
                if session is None:
                    return False
                state = dict(session.task_state) if isinstance(session.task_state, dict) else {}
                active = set(state.get('active_turn_ids') or [])
                if status == 'in_progress':
                    active.add(turn_id)
                else:
                    active.discard(turn_id)
                state['active_turn_ids'] = sorted(active)
                state['active_turn_id'] = (
                    turn_id if status == 'in_progress' else (sorted(active)[0] if active else None)
                )
                state['status'] = 'in_progress' if active else status
                if objective is not None:
                    state['objective'] = objective
                if user_message_id:
                    state['latest_user_message_id'] = user_message_id
                state['updated_at'] = utcnow().isoformat()
                session.task_state = state
                session.updated_at = utcnow()
            return True

    async def create_turn(
        self,
        session_id: str,
        request_payload: dict,
        *,
        model_id: str | None = None,
        chat_message_id: str | None = None,
        user_message_id: str | None = None,
        idempotency_key: str | None = None,
        tool_contract_version: str = 'pending',
        skill_snapshot_version: str = 'pending',
    ) -> AgentTurn:
        if not isinstance(request_payload, dict):
            raise ValueError('request_payload must be an object')
        if idempotency_key is not None and (not idempotency_key or len(idempotency_key) > 255):
            raise ValueError('idempotency_key must contain 1 to 255 characters')
        async with self._session_factory() as db:
            async with db.begin():
                session = await db.scalar(select(AgentSession).where(AgentSession.id == session_id).with_for_update())
                if session is None:
                    raise HTTPException(status_code=404, detail='Agent session not found.')
                if idempotency_key is not None:
                    existing = await db.scalar(
                        select(AgentTurn).where(
                            AgentTurn.session_id == session_id,
                            AgentTurn.idempotency_key == idempotency_key,
                        )
                    )
                    if existing is not None:
                        same_request = (
                            existing.request_payload == request_payload
                            and existing.model_id == model_id
                            and existing.chat_message_id == chat_message_id
                            and existing.user_message_id == user_message_id
                            and existing.engine_type == session.engine_type
                            and existing.engine_version == session.engine_version
                            and existing.tool_contract_version == tool_contract_version
                            and existing.skill_snapshot_version == skill_snapshot_version
                        )
                        if not same_request:
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    'code': 'idempotency_key_reused',
                                    'message': 'The idempotency key was already used for a different run request.',
                                },
                            )
                        turn = existing
                        duplicate = True
                    else:
                        duplicate = False
                else:
                    duplicate = False
                    turn = None
                if duplicate:
                    pass
                else:
                    sequence = session.next_turn_sequence
                    session.next_turn_sequence += 1
                    session.updated_at = utcnow()
                    turn_id = str(uuid4())
                    turn = AgentTurn(
                        id=turn_id,
                        session_id=session_id,
                        sequence=sequence,
                        status='queued',
                        request_payload=request_payload,
                        idempotency_key=idempotency_key,
                        engine_type=session.engine_type,
                        engine_version=session.engine_version,
                        engine_run_id=turn_id,
                        engine_thread_id=session.id,
                        tool_contract_version=tool_contract_version,
                        skill_snapshot_version=skill_snapshot_version,
                        model_id=model_id,
                        chat_message_id=chat_message_id,
                        user_message_id=user_message_id,
                        attempts=0,
                        next_event_sequence=2,
                        checkpoint_version=0,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    db.add(turn)
                    # The event has a foreign key to the new turn. Persist the
                    # parent row before adding dependent rows; SQLAlchemy does
                    # not infer this ordering without an ORM relationship.
                    await db.flush()
                    db.add(
                        AgentEvent(
                            id=str(uuid4()),
                            session_id=session_id,
                            turn_id=turn.id,
                            sequence=1,
                            event_type='turn.queued',
                            payload={'turn_id': turn.id, 'sequence': sequence},
                            created_at=utcnow(),
                        )
                    )
            if not duplicate:
                await db.refresh(turn)
            return turn

    async def find_resumable_turn(self, session_id: str, chat_message_id: str) -> AgentTurn | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentTurn)
                .where(
                    AgentTurn.session_id == session_id,
                    AgentTurn.chat_message_id == chat_message_id,
                    AgentTurn.status.in_(('waiting_approval', 'interrupted', 'paused')),
                )
                .order_by(AgentTurn.sequence.desc())
                .limit(1)
            )

    async def claim_turn_by_id(
        self,
        turn_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: dt.datetime | None = None,
    ) -> AgentTurn:
        if not worker_id:
            raise ValueError('worker_id is required')
        if lease_seconds < 1:
            raise ValueError('lease_seconds must be positive')
        current_time = now or utcnow()
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                if turn.status not in {'queued', 'interrupted', 'waiting_approval', 'paused'}:
                    raise HTTPException(status_code=409, detail='Agent turn cannot be claimed in its current state.')
                unknown = await db.scalar(
                    select(AgentToolInvocation.id)
                    .where(
                        AgentToolInvocation.turn_id == turn.id,
                        AgentToolInvocation.status == 'outcome_unknown',
                    )
                    .limit(1)
                )
                if unknown:
                    raise HTTPException(status_code=409, detail='An unresolved tool outcome blocks turn recovery.')
                turn.status = 'running'
                turn.worker_id = worker_id
                turn.lease_until = current_time + dt.timedelta(seconds=lease_seconds)
                turn.attempts += 1
                turn.started_at = turn.started_at or current_time
                turn.updated_at = current_time
                self._append_event_in_transaction(
                    db, turn, 'turn.started', {'turn_id': turn.id, 'attempt': turn.attempts}
                )
            await db.refresh(turn)
            return turn

    async def set_turn_status(
        self,
        turn_id: str,
        status: str,
        *,
        event_type: str,
        payload: dict | None = None,
        error: dict | None = None,
    ) -> AgentTurn:
        if status not in {'waiting_approval', 'paused', 'failed', 'interrupted', 'cancelled'}:
            raise ValueError(f'unsupported turn state: {status}')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                if turn.status in {'completed', 'failed', 'cancelled'}:
                    raise HTTPException(status_code=409, detail='Terminal turn state cannot be changed.')
                turn.status = status
                turn.worker_id = None
                turn.lease_until = None
                turn.error = error
                turn.updated_at = utcnow()
                if status in {'failed', 'cancelled'}:
                    turn.completed_at = turn.updated_at
                self._append_event_in_transaction(db, turn, event_type, payload or {'turn_id': turn_id})
            await db.refresh(turn)
            return turn

    async def has_unresolved_tool_calls(self, turn_id: str) -> bool:
        async with self._session_factory() as db:
            result = await db.scalar(
                select(AgentToolInvocation.id)
                .where(
                    AgentToolInvocation.turn_id == turn_id,
                    AgentToolInvocation.status.in_(('pending', 'running', 'waiting_approval', 'outcome_unknown')),
                )
                .limit(1)
            )
            return result is not None

    async def has_unknown_tool_outcomes(self, turn_id: str) -> bool:
        async with self._session_factory() as db:
            result = await db.scalar(
                select(AgentToolInvocation.id)
                .where(
                    AgentToolInvocation.turn_id == turn_id,
                    AgentToolInvocation.status == 'outcome_unknown',
                )
                .limit(1)
            )
            return result is not None

    async def get_owned_turn(self, turn_id: str, *, owner_id: str) -> AgentTurn | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentTurn)
                .join(AgentSession, AgentTurn.session_id == AgentSession.id)
                .where(AgentTurn.id == turn_id, AgentSession.owner_id == owner_id)
            )

    async def request_cancel_turn(self, turn_id: str, *, owner_id: str) -> AgentTurn:
        """Persist cancellation once; active handlers observe the durable request."""
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(
                    select(AgentTurn)
                    .join(AgentSession, AgentTurn.session_id == AgentSession.id)
                    .where(AgentTurn.id == turn_id, AgentSession.owner_id == owner_id)
                    .with_for_update()
                )
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                if turn.status in {'completed', 'failed', 'cancelled'} or turn.cancel_requested:
                    return turn
                if turn.status == 'running':
                    turn.cancel_requested = True
                    turn.updated_at = utcnow()
                    self._append_event_in_transaction(
                        db, turn, 'turn.cancel_requested', {'turn_id': turn.id}
                    )
                else:
                    unknown = await db.scalar(
                        select(AgentToolInvocation.id)
                        .where(
                            AgentToolInvocation.turn_id == turn.id,
                            AgentToolInvocation.status == 'outcome_unknown',
                        )
                        .limit(1)
                    )
                    if unknown:
                        turn.cancel_requested = True
                        turn.updated_at = utcnow()
                        self._append_event_in_transaction(
                            db, turn, 'turn.cancel_requested', {'turn_id': turn.id, 'recovery_required': True}
                        )
                    else:
                        turn.status = 'cancelled'
                        turn.completed_at = utcnow()
                        turn.updated_at = turn.completed_at
                        turn.worker_id = None
                        turn.lease_until = None
                        self._append_event_in_transaction(db, turn, 'turn.cancelled', {'turn_id': turn.id})
            await db.refresh(turn)
            return turn

    async def is_cancel_requested(self, turn_id: str) -> bool:
        async with self._session_factory() as db:
            turn = await db.get(AgentTurn, turn_id)
            return bool(turn and turn.cancel_requested)

    async def get_turn_status(self, turn_id: str, *, owner_id: str) -> dict[str, Any] | None:
        """Return an owner-scoped recovery snapshot without request or tool secrets."""
        async with self._session_factory() as db:
            row = await db.execute(
                select(AgentTurn, AgentSession)
                .join(AgentSession, AgentTurn.session_id == AgentSession.id)
                .where(AgentTurn.id == turn_id, AgentSession.owner_id == owner_id)
            )
            result = row.one_or_none()
            if result is None:
                return None
            turn, session = result
            latest_sequence = max(0, turn.next_event_sequence - 1)
            checkpoint = await db.scalar(
                select(AgentCheckpoint)
                .where(AgentCheckpoint.turn_id == turn.id)
                .order_by(AgentCheckpoint.version.desc())
                .limit(1)
            )
            return {
                'turn_id': turn.id,
                'session_id': session.id,
                'chat_id': session.chat_id,
                'status': turn.status,
                'cancel_requested': turn.cancel_requested,
                'latest_sequence': latest_sequence,
                'checkpoint_version': checkpoint.version if checkpoint else 0,
                'conversation_binding': {
                    'engine_type': session.engine_type,
                    'engine_version': session.engine_version,
                },
                'run_binding': {
                    'engine_type': turn.engine_type,
                    'engine_version': turn.engine_version,
                    'engine_run_id': turn.engine_run_id,
                    'engine_thread_id': turn.engine_thread_id,
                    'tool_contract_version': turn.tool_contract_version,
                    'skill_snapshot_version': turn.skill_snapshot_version,
                },
            }

    async def append_event(self, turn_id: str, event_type: str, payload: dict) -> AgentEvent:
        if not event_type or len(event_type) > 96:
            raise ValueError('event_type must contain 1 to 96 characters')
        if not isinstance(payload, dict):
            raise ValueError('event payload must be an object')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                event = self._append_event_in_transaction(db, turn, event_type, payload)
            await db.refresh(event)
            return event

    @staticmethod
    def _append_event_in_transaction(db: AsyncSession, turn: AgentTurn, event_type: str, payload: dict) -> AgentEvent:
        sequence = turn.next_event_sequence
        turn.next_event_sequence += 1
        turn.updated_at = utcnow()
        event = AgentEvent(
            id=str(uuid4()),
            session_id=turn.session_id,
            turn_id=turn.id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            created_at=utcnow(),
        )
        db.add(event)
        return event

    async def list_events(self, turn_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[AgentEvent]:
        limit = max(1, min(int(limit), 1000))
        async with self._session_factory() as db:
            result = await db.scalars(
                select(AgentEvent)
                .where(AgentEvent.turn_id == turn_id, AgentEvent.sequence > max(0, after_sequence))
                .order_by(AgentEvent.sequence)
                .limit(limit)
            )
            return list(result)

    async def get_event_page(self, turn_id: str, *, after_sequence: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read a durable, ordered event page and report when replay needs a state snapshot."""
        cursor = max(0, int(after_sequence))
        limit = max(1, min(int(limit), 1000))
        async with self._session_factory() as db:
            turn = await db.get(AgentTurn, turn_id)
            if turn is None:
                raise HTTPException(status_code=404, detail='Agent turn not found.')
            latest_sequence = max(0, turn.next_event_sequence - 1)
            first_sequence = await db.scalar(
                select(func.min(AgentEvent.sequence)).where(AgentEvent.turn_id == turn_id)
            )
            result = await db.scalars(
                select(AgentEvent)
                .where(AgentEvent.turn_id == turn_id, AgentEvent.sequence > cursor)
                .order_by(AgentEvent.sequence)
                .limit(limit)
            )
            events = list(result)

        expected = cursor + 1
        gap_detected = bool(first_sequence is not None and cursor < first_sequence - 1)
        for event in events:
            if event.sequence != expected:
                gap_detected = True
            expected = event.sequence + 1
        if not events and cursor < latest_sequence:
            gap_detected = True
        last_sequence = events[-1].sequence if events else cursor
        return {
            'events': events,
            'first_available_sequence': first_sequence,
            'latest_sequence': latest_sequence,
            'next_after_sequence': latest_sequence if gap_detected else last_sequence,
            'has_more': last_sequence < latest_sequence,
            'gap_detected': gap_detected,
            'snapshot_required': gap_detected,
        }

    async def get_run_binding(self, turn_id: str) -> RunBinding:
        async with self._session_factory() as db:
            turn = await db.get(AgentTurn, turn_id)
            if turn is None:
                raise HTTPException(status_code=404, detail='Agent turn not found.')
            session = await db.get(AgentSession, turn.session_id)
            if session is None:
                raise HTTPException(status_code=404, detail='Agent session not found.')
            return RunBinding.from_records(session, turn)

    async def bind_run_contract(
        self, turn_id: str, *, tool_contract_version: str, skill_snapshot_version: str
    ) -> RunBinding:
        if not tool_contract_version or not skill_snapshot_version:
            raise ValueError('tool and skill snapshot versions are required')
        if len(tool_contract_version) > 255 or len(skill_snapshot_version) > 255:
            raise ValueError('runtime contract versions must not exceed 255 characters')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                session = await db.get(AgentSession, turn.session_id)
                if session is None:
                    raise HTTPException(status_code=404, detail='Agent session not found.')
                RunBinding.from_records(session, turn)
                current_pair = (turn.tool_contract_version, turn.skill_snapshot_version)
                requested_pair = (tool_contract_version, skill_snapshot_version)
                if current_pair == ('pending', 'pending'):
                    turn.tool_contract_version, turn.skill_snapshot_version = requested_pair
                    turn.updated_at = utcnow()
                    self._append_event_in_transaction(
                        db,
                        turn,
                        'run.binding_finalized',
                        {
                            'tool_contract_version': tool_contract_version,
                            'skill_snapshot_version': skill_snapshot_version,
                        },
                    )
                elif current_pair != requested_pair:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            'code': 'runtime_snapshot_mismatch',
                            'message': 'A run cannot change its tool contract or skill snapshot while resuming.',
                        },
                    )
                return RunBinding.from_records(session, turn)

    async def mark_event_published(self, event_id: str) -> bool:
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    update(AgentEvent)
                    .where(AgentEvent.id == event_id, AgentEvent.published_at.is_(None))
                    .values(published_at=utcnow())
                )
                return result.rowcount == 1

    async def save_checkpoint(self, turn_id: str, data: dict, *, kind: str = 'turn') -> AgentCheckpoint:
        if not isinstance(data, dict):
            raise ValueError('checkpoint data must be an object')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                turn.checkpoint_version += 1
                turn.updated_at = utcnow()
                checkpoint = AgentCheckpoint(
                    id=str(uuid4()),
                    session_id=turn.session_id,
                    turn_id=turn.id,
                    version=turn.checkpoint_version,
                    kind=kind,
                    data=data,
                    created_at=utcnow(),
                )
                db.add(checkpoint)
            await db.refresh(checkpoint)
            return checkpoint

    async def latest_checkpoint(self, turn_id: str, *, kind: str | None = None) -> AgentCheckpoint | None:
        async with self._session_factory() as db:
            query = select(AgentCheckpoint).where(AgentCheckpoint.turn_id == turn_id)
            if kind is not None:
                query = query.where(AgentCheckpoint.kind == kind)
            return await db.scalar(query.order_by(AgentCheckpoint.version.desc()).limit(1))

    async def claim_turn(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: dt.datetime | None = None,
    ) -> AgentTurn | None:
        if not worker_id:
            raise ValueError('worker_id is required')
        if lease_seconds < 1:
            raise ValueError('lease_seconds must be positive')
        current_time = now or utcnow()
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(
                    select(AgentTurn)
                    .where(AgentTurn.status.in_(('queued', 'interrupted')))
                    .order_by(AgentTurn.created_at, AgentTurn.sequence)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if turn is None:
                    return None
                turn.status = 'running'
                turn.worker_id = worker_id
                turn.lease_until = current_time + dt.timedelta(seconds=lease_seconds)
                turn.attempts += 1
                turn.started_at = turn.started_at or current_time
                turn.updated_at = current_time
                self._append_event_in_transaction(
                    db, turn, 'turn.started', {'turn_id': turn.id, 'attempt': turn.attempts}
                )
            await db.refresh(turn)
            return turn

    async def heartbeat_turn(
        self,
        turn_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: dt.datetime | None = None,
    ) -> bool:
        if lease_seconds < 1:
            raise ValueError('lease_seconds must be positive')
        current_time = now or utcnow()
        async with self._session_factory() as db:
            result = await db.execute(
                update(AgentTurn)
                .where(
                    AgentTurn.id == turn_id,
                    AgentTurn.status == 'running',
                    AgentTurn.worker_id == worker_id,
                )
                .values(lease_until=current_time + dt.timedelta(seconds=lease_seconds), updated_at=current_time)
            )
            await db.commit()
            return bool(result.rowcount)

    async def recover_expired_turns(self, *, now: dt.datetime | None = None) -> int:
        current_time = now or utcnow()
        recovered = 0
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.scalars(
                    select(AgentTurn)
                    .where(
                        AgentTurn.status == 'running',
                        AgentTurn.lease_until.is_not(None),
                        AgentTurn.lease_until <= current_time,
                    )
                    .order_by(AgentTurn.created_at)
                    .with_for_update(skip_locked=True)
                )
                for turn in result:
                    invocations = await db.scalars(
                        select(AgentToolInvocation)
                        .where(AgentToolInvocation.turn_id == turn.id, AgentToolInvocation.status == 'running')
                        .with_for_update()
                    )
                    uncertain: list[str] = []
                    retryable: list[str] = []
                    for invocation in invocations:
                        if invocation.retry_safe:
                            invocation.status = 'pending'
                            invocation.started_at = None
                            invocation.updated_at = current_time
                            retryable.append(invocation.call_id)
                        else:
                            invocation.status = 'outcome_unknown'
                            invocation.error = {'code': 'tool_outcome_unknown', 'message': 'Worker lease expired.'}
                            invocation.updated_at = current_time
                            uncertain.append(invocation.call_id)
                    turn.status = 'paused' if uncertain else 'cancelled' if turn.cancel_requested else 'interrupted'
                    turn.worker_id = None
                    turn.lease_until = None
                    turn.updated_at = current_time
                    if turn.status == 'cancelled':
                        turn.completed_at = current_time
                    session = await db.scalar(
                        select(AgentSession).where(AgentSession.id == turn.session_id).with_for_update()
                    )
                    if session is not None:
                        state = dict(session.task_state) if isinstance(session.task_state, dict) else {}
                        active = set(state.get('active_turn_ids') or [])
                        active.discard(turn.id)
                        state['active_turn_ids'] = sorted(active)
                        state['active_turn_id'] = sorted(active)[0] if active else None
                        state['status'] = 'in_progress' if active else turn.status
                        state['updated_at'] = current_time.isoformat()
                        session.task_state = state
                        session.updated_at = current_time
                    self._append_event_in_transaction(
                        db,
                        turn,
                        'turn.recovery_required'
                        if uncertain
                        else 'turn.cancelled'
                        if turn.status == 'cancelled'
                        else 'turn.interrupted',
                        {
                            'turn_id': turn.id,
                            'uncertain_tool_calls': uncertain,
                            'retryable_tool_calls': retryable,
                        },
                    )
                    recovered += 1
        return recovered

    async def interrupt_running_tool_calls(self, turn_id: str) -> bool:
        """Release retry-safe reads; quarantine interrupted side effects as unknown."""
        current_time = utcnow()
        unknown = False
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    return False
                invocations = await db.scalars(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn_id, AgentToolInvocation.status == 'running')
                    .with_for_update()
                )
                for invocation in invocations:
                    if invocation.retry_safe:
                        invocation.status = 'pending'
                        invocation.started_at = None
                        event_type = 'tool.retry_scheduled'
                    else:
                        invocation.status = 'outcome_unknown'
                        invocation.error = {'code': 'tool_outcome_unknown', 'message': 'Execution was interrupted.'}
                        event_type = 'tool.outcome_unknown'
                        unknown = True
                    invocation.updated_at = current_time
                    self._append_event_in_transaction(
                        db,
                        turn,
                        event_type,
                        {'call_id': invocation.call_id, 'tool_name': invocation.tool_name},
                    )
        return unknown

    async def begin_tool_call(
        self,
        turn_id: str,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict,
        retry_safe: bool,
        idempotency_key: str | None = None,
    ) -> tuple[AgentToolInvocation, bool]:
        if not call_id or not tool_name:
            raise ValueError('call_id and tool_name are required')
        if not isinstance(arguments, dict):
            raise ValueError('tool arguments must be an object')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                if turn.status != 'running':
                    raise HTTPException(status_code=409, detail='Agent turn is not running.')
                invocation = await db.scalar(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn.id, AgentToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                current_time = utcnow()
                acquired = False
                if invocation is None:
                    invocation = AgentToolInvocation(
                        id=str(uuid4()),
                        session_id=turn.session_id,
                        turn_id=turn.id,
                        call_id=call_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        status='running',
                        retry_safe=retry_safe,
                        idempotency_key=idempotency_key,
                        attempt_count=1,
                        started_at=current_time,
                        created_at=current_time,
                        updated_at=current_time,
                    )
                    db.add(invocation)
                    acquired = True
                else:
                    if invocation.tool_name != tool_name or invocation.arguments != arguments:
                        raise HTTPException(
                            status_code=409, detail='Tool call identity was reused with different input.'
                        )
                    if invocation.idempotency_key != idempotency_key:
                        raise HTTPException(status_code=409, detail='Tool retry policy changed during recovery.')
                    if invocation.retry_safe != retry_safe:
                        if (
                            invocation.status == 'pending'
                            and invocation.approval_status == 'approved'
                            and invocation.attempt_count == 0
                        ):
                            invocation.retry_safe = retry_safe
                        else:
                            raise HTTPException(status_code=409, detail='Tool retry policy changed during recovery.')
                    if invocation.status == 'pending' and (retry_safe or invocation.approval_status == 'approved'):
                        invocation.status = 'running'
                        invocation.attempt_count += 1
                        invocation.started_at = current_time
                        invocation.updated_at = current_time
                        acquired = True
                if acquired:
                    self._append_event_in_transaction(
                        db,
                        turn,
                        'tool.started' if invocation.attempt_count == 1 else 'tool.retried',
                        {
                            'call_id': call_id,
                            'tool_name': tool_name,
                            'attempt': invocation.attempt_count,
                        },
                    )
            await db.refresh(invocation)
            return invocation, acquired

    async def record_tool_approval(
        self,
        turn_id: str,
        *,
        owner_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict,
        decision: str,
        approved_by: str,
        retry_safe: bool,
        idempotency_key: str,
    ) -> AgentToolInvocation:
        if decision not in {'approved', 'rejected'}:
            raise ValueError('tool approval decision must be approved or rejected')
        if not call_id or not tool_name or not isinstance(arguments, dict):
            raise ValueError('call_id, tool_name, and object arguments are required')
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                session = await db.get(AgentSession, turn.session_id)
                if session is None or session.owner_id != owner_id:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                if turn.status not in {'running', 'waiting_approval', 'interrupted'}:
                    raise HTTPException(status_code=409, detail='Agent turn is not awaiting tool approval.')

                invocation = await db.scalar(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn.id, AgentToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if invocation is None:
                    invocation = AgentToolInvocation(
                        id=str(uuid4()),
                        session_id=turn.session_id,
                        turn_id=turn.id,
                        call_id=call_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        status='pending' if decision == 'approved' else 'failed',
                        retry_safe=retry_safe,
                        idempotency_key=idempotency_key,
                        attempt_count=0,
                        approval_status=decision,
                        approved_by=approved_by,
                        error={'code': 'tool_rejected'} if decision == 'rejected' else None,
                        completed_at=utcnow() if decision == 'rejected' else None,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    db.add(invocation)
                else:
                    if invocation.tool_name != tool_name or invocation.arguments != arguments:
                        raise HTTPException(
                            status_code=409, detail='Tool call identity was reused with different input.'
                        )
                    if invocation.approval_status == decision:
                        await db.refresh(invocation)
                        return invocation
                    if invocation.status not in {'pending', 'waiting_approval'}:
                        raise HTTPException(status_code=409, detail='Tool invocation can no longer be approved.')
                    invocation.status = 'pending' if decision == 'approved' else 'failed'
                    invocation.approval_status = decision
                    invocation.approved_by = approved_by
                    invocation.error = {'code': 'tool_rejected'} if decision == 'rejected' else None
                    invocation.completed_at = utcnow() if decision == 'rejected' else None
                    invocation.updated_at = utcnow()
                self._append_event_in_transaction(
                    db,
                    turn,
                    f'tool.{decision}',
                    {'call_id': call_id, 'tool_name': tool_name, 'approved_by': approved_by},
                )
            await db.refresh(invocation)
            return invocation

    async def complete_tool_call(
        self,
        turn_id: str,
        call_id: str,
        result_data: Any,
        *,
        result_artifact_id: str | None = None,
    ) -> AgentToolInvocation:
        async with self._session_factory() as db:
            async with db.begin():
                invocation = await db.scalar(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn_id, AgentToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if invocation is None:
                    raise HTTPException(status_code=404, detail='Tool invocation not found.')
                if invocation.status == 'outcome_unknown':
                    raise HTTPException(status_code=409, detail='Unknown tool outcome requires reconciliation.')
                if invocation.status != 'succeeded':
                    if invocation.status != 'running':
                        raise HTTPException(status_code=409, detail='Tool invocation is not running.')
                    invocation.status = 'succeeded'
                    invocation.result_data = result_data
                    invocation.result_artifact_id = result_artifact_id
                    invocation.completed_at = utcnow()
                    invocation.updated_at = invocation.completed_at
                    turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                    if turn:
                        self._append_event_in_transaction(
                            db,
                            turn,
                            'tool.completed',
                            {
                                'call_id': call_id,
                                'artifact_id': result_artifact_id,
                            },
                        )
            await db.refresh(invocation)
            return invocation

    async def fail_tool_call(
        self, turn_id: str, call_id: str, error: dict, *, uncertain: bool = False
    ) -> AgentToolInvocation:
        async with self._session_factory() as db:
            async with db.begin():
                invocation = await db.scalar(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn_id, AgentToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if invocation is None:
                    raise HTTPException(status_code=404, detail='Tool invocation not found.')
                invocation.status = 'outcome_unknown' if uncertain else 'failed'
                invocation.error = error
                invocation.completed_at = utcnow()
                invocation.updated_at = invocation.completed_at
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn:
                    if uncertain:
                        turn.status = 'paused'
                        turn.worker_id = None
                        turn.lease_until = None
                    self._append_event_in_transaction(
                        db,
                        turn,
                        'tool.outcome_unknown' if uncertain else 'tool.failed',
                        {'call_id': call_id, 'tool_name': invocation.tool_name, 'error': error},
                    )
            await db.refresh(invocation)
            return invocation

    async def release_tool_call_for_retry(self, turn_id: str, call_id: str, error: dict) -> AgentToolInvocation:
        async with self._session_factory() as db:
            async with db.begin():
                invocation = await db.scalar(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.turn_id == turn_id, AgentToolInvocation.call_id == call_id)
                    .with_for_update()
                )
                if invocation is None:
                    raise HTTPException(status_code=404, detail='Tool invocation not found.')
                if not invocation.retry_safe:
                    raise HTTPException(status_code=409, detail='Only retry-safe tool calls can be released.')
                if invocation.status == 'running':
                    invocation.status = 'pending'
                    invocation.error = error
                    invocation.started_at = None
                    invocation.updated_at = utcnow()
                    turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                    if turn:
                        turn.status = 'interrupted'
                        turn.worker_id = None
                        turn.lease_until = None
                        self._append_event_in_transaction(
                            db, turn, 'tool.retry_scheduled', {'call_id': call_id, 'error': error}
                        )
            await db.refresh(invocation)
            return invocation

    async def get_tool_call(self, turn_id: str, call_id: str) -> AgentToolInvocation | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentToolInvocation).where(
                    AgentToolInvocation.turn_id == turn_id,
                    AgentToolInvocation.call_id == call_id,
                )
            )

    async def get_turn(self, turn_id: str) -> AgentTurn | None:
        async with self._session_factory() as db:
            return await db.get(AgentTurn, turn_id)

    async def complete_turn(self, turn_id: str, *, result: dict | None = None) -> AgentTurn:
        async with self._session_factory() as db:
            async with db.begin():
                turn = await db.scalar(select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update())
                if turn is None:
                    raise HTTPException(status_code=404, detail='Agent turn not found.')
                unresolved = await db.scalar(
                    select(AgentToolInvocation.id)
                    .where(
                        AgentToolInvocation.turn_id == turn.id,
                        AgentToolInvocation.status.in_(('pending', 'running', 'waiting_approval', 'outcome_unknown')),
                    )
                    .limit(1)
                )
                if unresolved:
                    raise HTTPException(status_code=409, detail='Agent turn has unresolved tool calls.')
                turn.status = 'completed'
                turn.worker_id = None
                turn.lease_until = None
                turn.completed_at = utcnow()
                turn.updated_at = turn.completed_at
                if turn.cancel_requested:
                    turn.status = 'cancelled'
                    self._append_event_in_transaction(db, turn, 'turn.cancelled', {'turn_id': turn.id})
                else:
                    self._append_event_in_transaction(db, turn, 'turn.completed', {'turn_id': turn.id, **(result or {})})
            await db.refresh(turn)
            return turn

    async def record_artifact(self, artifact: AgentArtifact) -> AgentArtifact:
        async with self._session_factory() as db:
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)
            return artifact

    async def get_artifact(self, artifact_id: str, *, owner_id: str) -> AgentArtifact | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(AgentArtifact).where(AgentArtifact.id == artifact_id, AgentArtifact.owner_id == owner_id)
            )
