"""Durable Transactional Outbox Engine with Multi-Worker Lease Fencing.

Features:
1. Transactional event creation within caller's business transaction.
2. Concurrent claim loop using SELECT ... FOR UPDATE SKIP LOCKED.
3. Monotonic lease_generation fencing tokens preventing stale worker overwrites.
4. Exponential backoff with jitter on delivery failure.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from open_webui.mini_hermes.v2.models import MhOutbox, utcnow

log = logging.getLogger(__name__)


def insert_outbox_event(
    session: AsyncSession,
    *,
    event_key: str,
    event_type: str,
    payload: dict[str, Any],
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
) -> MhOutbox:
    """Insert a new outbox event inside the caller's active database transaction."""
    event = MhOutbox(
        id=uuid.uuid4(),
        event_key=event_key,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload_json=payload,
        delivery_attempts=0,
        max_attempts=10,
        lease_worker_id=None,
        lease_generation=0,
        lease_expires_at=None,
        published_at=None,
        created_at=utcnow(),
    )
    session.add(event)
    return event


class OutboxDispatcher:
    """Worker dispatcher for reliably publishing outbox events with lease fencing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str | None = None,
        lease_ttl_seconds: int = 30,
        batch_size: int = 10,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_ttl_seconds = lease_ttl_seconds
        self.batch_size = batch_size

    async def claim_batch(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Claim a batch of pending events using SELECT ... FOR UPDATE SKIP LOCKED.

        Increments lease_generation fencing token on each claimed row.
        """
        now = utcnow()
        stmt = (
            select(MhOutbox)
            .where(
                MhOutbox.published_at.is_(None),
                or_(
                    MhOutbox.lease_expires_at.is_(None),
                    MhOutbox.lease_expires_at <= now,
                ),
                MhOutbox.delivery_attempts < MhOutbox.max_attempts,
            )
            .order_by(MhOutbox.created_at.asc())
            .limit(self.batch_size)
            .with_for_update(skip_locked=True)
        )

        records = (await session.scalars(stmt)).all()
        claimed: list[dict[str, Any]] = []

        for ev in records:
            ev.delivery_attempts += 1
            ev.lease_worker_id = self.worker_id
            ev.lease_generation += 1  # Fencing token increment
            ev.lease_expires_at = now + timedelta(seconds=self.lease_ttl_seconds)

            claimed.append({
                "id": ev.id,
                "generation": ev.lease_generation,
                "event_key": ev.event_key,
                "event_type": ev.event_type,
                "payload": ev.payload_json,
                "attempts": ev.delivery_attempts,
            })

        await session.flush()
        return claimed

    async def confirm_published(
        self,
        session: AsyncSession,
        event_id: uuid.UUID,
        claimed_generation: int,
    ) -> bool:
        """Mark an event published, strictly guarded by lease_generation fencing token.

        Returns True if updated, or False if fenced out by a newer lease generation or owner.
        """
        stmt = (
            update(MhOutbox)
            .where(
                MhOutbox.id == event_id,
                MhOutbox.lease_generation == claimed_generation,
                MhOutbox.lease_worker_id == self.worker_id,
                MhOutbox.published_at.is_(None),
            )
            .values(
                published_at=utcnow(),
                lease_expires_at=None,
                last_error=None,
            )
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            log.warning(
                "Lease fencing rejected published update for event %s (stale generation %s, worker %s)",
                event_id,
                claimed_generation,
                self.worker_id,
            )
            return False
        return True

    async def record_failure(
        self,
        session: AsyncSession,
        event_id: uuid.UUID,
        claimed_generation: int,
        error_message: str,
        attempts: int,
    ) -> None:
        """Record delivery failure with exponential backoff and jitter."""
        backoff = min(3600, 2 ** min(attempts, 10)) + random.uniform(0.1, 1.0)
        next_attempt_at = utcnow() + timedelta(seconds=backoff)

        stmt = (
            update(MhOutbox)
            .where(
                MhOutbox.id == event_id,
                MhOutbox.lease_generation == claimed_generation,
            )
            .values(
                lease_expires_at=next_attempt_at,
                last_error=error_message[:1000],
            )
        )
        await session.execute(stmt)

    async def process_one_batch(
        self,
        publish_handler: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> int:
        """Claim a batch, invoke handler for each item, and update status with fencing."""
        async with self.session_factory() as session:
            async with session.begin():
                claimed = await self.claim_batch(session)

        if not claimed:
            return 0

        success_count = 0
        for item in claimed:
            try:
                await publish_handler(item["event_type"], item["payload"])
                async with self.session_factory() as session:
                    async with session.begin():
                        confirmed = await self.confirm_published(
                            session, item["id"], item["generation"]
                        )
                        if confirmed:
                            success_count += 1
            except Exception as exc:
                log.exception("Outbox event %s delivery failed: %s", item["id"], exc)
                async with self.session_factory() as session:
                    async with session.begin():
                        await self.record_failure(
                            session,
                            item["id"],
                            item["generation"],
                            str(exc),
                            item["attempts"],
                        )

        return success_count
