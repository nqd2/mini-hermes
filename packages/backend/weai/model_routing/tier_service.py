"""Transactional entitlement and dispatch accounting for Agent Runtime."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from weai.internal.db import Base, get_async_db
from weai.model_routing.tier_schema_v1 import tier_tables
from weai.models.auths import Auth
from weai.models.users import User

plans, dispatches, selections = tier_tables(Base.metadata)
Tier = Literal['free', 'plus']
DEFAULT_PLUS_MONTHLY_LIMIT = 1000
SIZES = ('small', 'medium', 'large')
GROUPS = {f'agent-runtime-{tier}-{size}': tier for tier in ('free', 'plus') for size in SIZES}
ERROR_MESSAGES = {
    'entitlement_missing': 'Choose a model available on your WeAI plan.',
    'quota_exhausted': 'You have used all requests in your WeAI allowance.',
    'free_capacity_exhausted': 'The shared Free allowance is currently exhausted.',
    'model_unavailable': 'No model in this group can handle the request right now.',
}


def policy_error(code: str, status: int, reset_at: datetime | None = None) -> HTTPException:
    detail = {'code': code}
    if code in ERROR_MESSAGES:
        detail['message'] = ERROR_MESSAGES[code]
    headers = None
    if reset_at is not None:
        reset_at = reset_at.astimezone(timezone.utc)
        detail['reset_at'] = reset_at.isoformat()
        if code in ERROR_MESSAGES:
            detail['message'] += f' Try again after {reset_at:%Y-%m-%d %H:%M UTC}.'
        seconds = max(1, int((reset_at - datetime.now(timezone.utc)).total_seconds()) + 1)
        headers = {'Retry-After': str(seconds)}
    return HTTPException(status_code=status, detail=detail, headers=headers)


def validate_plan_inputs(tier: Tier, limit: int | None) -> None:
    if (
        (tier == 'free' and limit is not None)
        or (tier == 'plus' and (type(limit) is not int or not 0 < limit <= 2**31 - 1))
        or tier not in {'free', 'plus'}
    ):
        raise policy_error('invalid_plan', 422)


@dataclass(frozen=True)
class PlanSnapshot:
    tier: Tier
    monthly_request_limit: int | None


@dataclass(frozen=True)
class UsageSnapshot(PlanSnapshot):
    used: int
    remaining: int
    reset_at: datetime


@dataclass(frozen=True)
class DispatchContext:
    dispatch_key: str
    user_id: str
    group_id: str
    model_id: str
    connection_id: str
    run_id: str | None = None
    free_account: str | None = None
    free_user_daily_limit: int = 2
    free_account_daily_limit: int = 50
    free_account_minute_limit: int = 20
    is_background_task: bool = False


def upstream_free_reset(status, headers, now) -> datetime | None:
    if status != 429 or str(headers.get('X-RateLimit-Remaining')) != '0':
        return None
    epoch = headers.get('X-RateLimit-Reset')
    try:
        value = int(epoch)
        reset = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return reset if now < reset <= now + timedelta(days=1) else None


def utc_windows(now: datetime):
    now = now.astimezone(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute = now.replace(second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return day, minute, month, next_month


class TierRepository:
    def __init__(self, session_factory=get_async_db):
        self.session_factory = session_factory

    async def _lock_identity(self, db, user_id: str, *, include_pending=False):
        role = await db.scalar(
            select(User.role)
            .join(Auth, Auth.id == User.id)
            .where(User.id == user_id, Auth.active.is_(True))
            .with_for_update(of=(User, Auth))
        )
        if role not in ({'user', 'admin', 'pending'} if include_pending else {'user', 'admin'}):
            raise policy_error('entitlement_missing', 403)
        return role

    async def _plan(self, db, user_id, now) -> PlanSnapshot:
        await db.execute(
            insert(plans)
            .values(user_id=user_id, tier='free', updated_at=now)
            .on_conflict_do_nothing(index_elements=[plans.c.user_id])
        )
        row = (await db.execute(select(plans).where(plans.c.user_id == user_id).with_for_update())).one()
        return PlanSnapshot(row.tier, row.monthly_request_limit)

    async def get_plan(self, user_id: str, *, include_pending=False) -> PlanSnapshot:
        async with self.session_factory() as db, db.begin():
            await self._lock_identity(db, user_id, include_pending=include_pending)
            return await self._plan(db, user_id, datetime.now(timezone.utc))

    async def get_plans_by_user_ids(self, user_ids: list[str], db=None) -> dict[str, PlanSnapshot]:
        if not user_ids:
            return {}
        unique_ids = list(dict.fromkeys(user_ids))
        stmt = select(plans.c.user_id, plans.c.tier, plans.c.monthly_request_limit).where(
            plans.c.user_id.in_(unique_ids)
        )
        if db is not None:
            rows = (await db.execute(stmt)).all()
        else:
            async with self.session_factory() as session:
                rows = (await session.execute(stmt)).all()
        result: dict[str, PlanSnapshot] = {uid: PlanSnapshot('free', None) for uid in unique_ids}
        for row in rows:
            result[row.user_id] = PlanSnapshot(row.tier, row.monthly_request_limit)
        return result

    async def set_plan_in_db(
        self, db, user_id: str, tier: Tier, limit: int | None, actor_id: str
    ) -> PlanSnapshot:
        validate_plan_inputs(tier, limit)
        await db.flush()
        await self._lock_identity(db, user_id, include_pending=True)
        now = datetime.now(timezone.utc)
        values = dict(tier=tier, monthly_request_limit=limit, updated_at=now, updated_by=actor_id)
        await db.execute(
            insert(plans)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(index_elements=[plans.c.user_id], set_=values)
        )
        return PlanSnapshot(tier, limit)

    async def set_plan(self, user_id: str, tier: Tier, limit: int | None, actor_id: str) -> PlanSnapshot:
        validate_plan_inputs(tier, limit)
        async with self.session_factory() as db, db.begin():
            return await self.set_plan_in_db(db, user_id, tier, limit, actor_id)

    async def _user_count(self, db, user_id, tier, start, end) -> int:
        # Count distinct user messages (turns) rather than every internal tool-call LLM dispatch,
        # excluding background tasks (bg:*) and upstream 429 failures.
        return await db.scalar(
            select(func.count(func.distinct(func.coalesce(dispatches.c.run_id, dispatches.c.dispatch_key))))
            .select_from(dispatches)
            .where(
                dispatches.c.user_id == user_id,
                dispatches.c.group_id.in_([group for group, group_tier in GROUPS.items() if group_tier == tier]),
                dispatches.c.provider_status.is_distinct_from(429),
                (dispatches.c.run_id.is_(None)) | (~dispatches.c.run_id.like('bg:%')),
                dispatches.c.created_at >= start,
                dispatches.c.created_at < end,
            )
        )

    async def _run_already_admitted(self, db, user_id, tier, run_id: str | None, start, end) -> bool:
        if not run_id or run_id.startswith('bg:'):
            return False
        return (
            await db.scalar(
                select(dispatches.c.dispatch_key)
                .where(
                    dispatches.c.user_id == user_id,
                    dispatches.c.group_id.in_([group for group, group_tier in GROUPS.items() if group_tier == tier]),
                    dispatches.c.run_id == run_id,
                    dispatches.c.provider_status.is_distinct_from(429),
                    dispatches.c.created_at >= start,
                    dispatches.c.created_at < end,
                )
                .limit(1)
            )
            is not None
        )

    async def get_usage(self, user_id: str, now: datetime, free_daily_limit: int) -> UsageSnapshot:
        day, _, month, next_month = utc_windows(now)
        async with self.session_factory() as db, db.begin():
            await self._lock_identity(db, user_id)
            plan = await self._plan(db, user_id, now)
            if plan.tier == 'plus':
                start, end, limit = month, next_month, plan.monthly_request_limit
            else:
                start, end, limit = day, day + timedelta(days=1), free_daily_limit
            used = await self._user_count(db, user_id, plan.tier, start, end)
            return UsageSnapshot(plan.tier, plan.monthly_request_limit, used, max(0, limit - used), end)

    async def _enforce_free_caps(self, db, context, now, is_admin):
        if not context.free_account:
            raise policy_error('model_unavailable', 503)
        # Account-wide locking spans minute/day rollover and all application workers.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:account, 0))"),
            {'account': f'agent-runtime-openrouter-free:{context.free_account}'},
        )
        provider_reset = await db.scalar(
            select(func.max(dispatches.c.shared_reset_at)).where(
                dispatches.c.free_account == context.free_account,
                dispatches.c.shared_reset_at > now,
            )
        )
        if provider_reset:
            raise policy_error('free_capacity_exhausted', 429, provider_reset)
        day, minute, _, _ = utc_windows(now)
        day_end = day + timedelta(days=1)
        minute_end = minute + timedelta(minutes=1)
        if (
            not is_admin
            and not context.is_background_task
            and GROUPS.get(context.group_id) == 'free'
            and not await self._run_already_admitted(db, context.user_id, 'free', context.run_id, day, day_end)
            and await self._user_count(db, context.user_id, 'free', day, day_end) >= context.free_user_daily_limit
        ):
            raise policy_error('quota_exhausted', 429, day_end)
        daily, current_minute = (
            await db.execute(
                select(
                    func.count(),
                    func.count().filter(dispatches.c.created_at >= minute, dispatches.c.created_at < minute_end),
                )
                .select_from(dispatches)
                .where(
                    dispatches.c.free_account == context.free_account,
                    dispatches.c.created_at >= day,
                    dispatches.c.created_at < day_end,
                )
            )
        ).one()
        if daily >= context.free_account_daily_limit:
            raise policy_error('free_capacity_exhausted', 429, day_end)
        if current_minute >= context.free_account_minute_limit:
            raise policy_error('free_capacity_exhausted', 429, minute_end)

    async def begin_dispatch(self, context: DispatchContext, fingerprint: str, now: datetime) -> None:
        """Reserve and mark sent atomically at the last step before transport.

        A committed row always counts, even if the process stops before receiving headers.
        Duplicate dispatches cannot invoke transport again.
        """
        async with self.session_factory() as db, db.begin():
            role = await self._lock_identity(db, context.user_id)
            previous = (
                await db.execute(select(dispatches).where(dispatches.c.dispatch_key == context.dispatch_key))
            ).one_or_none()
            if previous:
                if (
                    previous.user_id != context.user_id
                    or previous.group_id != context.group_id
                    or previous.fingerprint != fingerprint
                    or previous.run_id != context.run_id
                    or previous.connection_id != context.connection_id
                    or previous.model_id != context.model_id
                ):
                    raise policy_error('dispatch_key_conflict', 409)
                raise HTTPException(409, detail={'code': 'duplicate_dispatch', 'state': previous.state})
            tier = GROUPS.get(context.group_id)
            if role != 'admin':
                plan = await self._plan(db, context.user_id, now)
                if plan.tier != tier:
                    raise policy_error('entitlement_missing', 403)
                if tier == 'plus' and not context.is_background_task:
                    _, _, month, next_month = utc_windows(now)
                    if (
                        not await self._run_already_admitted(
                            db, context.user_id, tier, context.run_id, month, next_month
                        )
                        and await self._user_count(db, context.user_id, tier, month, next_month)
                        >= plan.monthly_request_limit
                    ):
                        raise policy_error('quota_exhausted', 429, next_month)
            if tier == 'free' or context.free_account:
                await self._enforce_free_caps(db, context, now, role == 'admin')
            inserted = await db.scalar(
                insert(dispatches)
                .values(
                    dispatch_key=context.dispatch_key,
                    user_id=context.user_id,
                    group_id=context.group_id,
                    model_id=context.model_id,
                    connection_id=context.connection_id,
                    run_id=context.run_id,
                    free_account=context.free_account,
                    fingerprint=fingerprint,
                    state='sent',
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[dispatches.c.dispatch_key])
                .returning(dispatches.c.dispatch_key)
            )
            if inserted is None:
                raise policy_error('duplicate_dispatch', 409)

    async def recently_rate_limited(self, connection_id: str, model_id: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        async with self.session_factory() as db:
            return (
                await db.scalar(
                    select(dispatches.c.dispatch_key)
                    .where(
                        dispatches.c.connection_id == connection_id,
                        dispatches.c.model_id == model_id,
                        dispatches.c.provider_status == 429,
                        dispatches.c.created_at >= cutoff,
                    )
                    .limit(1)
                )
                is not None
            )

    async def finish_dispatch(
        self,
        dispatch_key,
        *,
        status=None,
        request_id=None,
        latency_ms=None,
        shared_reset_at=None,
    ):
        async with self.session_factory() as db, db.begin():
            await db.execute(
                update(dispatches)
                .where(dispatches.c.dispatch_key == dispatch_key)
                .values(
                    state='responded' if status is not None else 'unknown',
                    provider_status=status,
                    shared_reset_at=shared_reset_at,
                    provider_request_id=request_id[:255] if request_id else None,
                    latency_ms=latency_ms,
                )
            )


tier_repository = TierRepository()


async def authorize_model(user, model_id) -> None:
    if user.role == 'admin':
        return
    plan = await tier_repository.get_plan(user.id)
    if not isinstance(model_id, str) or GROUPS.get(model_id) != plan.tier:
        raise policy_error('entitlement_missing', 403)


def virtual_model(model_id: str) -> dict:
    tier, size = model_id.removeprefix('agent-runtime-').split('-')
    return {
        'id': model_id,
        'name': f'{tier.title()} {size.title()}',
        'object': 'model',
        'owned_by': 'agent-runtime',
        'info': {'meta': {'capabilities': {'vision': True, 'function_calling': True}}},
    }


async def virtual_catalog(user) -> list[dict]:
    if getattr(user, 'role', None) == 'admin':
        return [virtual_model(model_id) for model_id in GROUPS]
    plan = await tier_repository.get_plan(user.id)
    return [virtual_model(model_id) for model_id, tier in GROUPS.items() if tier == plan.tier]
