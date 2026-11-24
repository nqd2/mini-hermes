"""Resolve approved model groups against a snapshot of provider connections."""

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from aiohttp import ClientError, ClientTimeout
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from weai.env import AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST
from weai.internal.db import get_async_db
from weai.model_routing.tier_service import (
    DispatchContext,
    FREE_GROUP_ID,
    GROUPS,
    LEGACY_FREE_GROUP_IDS,
    dispatches,
    normalize_group_id,
    policy_error,
    selections,
    tier_repository,
)
from weai.models.config import Config
from weai.utils.json_codec import JSONCodec
from weai.utils.model_ids import is_text_generation_model

CONFIG_KEY = 'weai.model_routing.v1'
OPENROUTER_URL = 'https://openrouter.ai/api/v1'
OPENROUTER_FREE_ROUTER_ID = 'openrouter/free'
FREE_ACCOUNT = 'openrouter'
FREE_429_MAX_FALLBACKS = 3
FREE_429_MAX_SAME_MODEL_RETRIES = 2
FREE_429_BACKOFF_BASE_SECONDS = 1.0
FREE_429_MAX_SINGLE_WAIT_SECONDS = 5.0
FREE_429_MAX_TOTAL_WAIT_SECONDS = 8.0
Capability = Literal['text', 'image', 'tools', 'structured_output', 'responses']
TierScope = Literal['free', 'plus', 'both']
VALID_TIER_SCOPES = frozenset({'free', 'plus', 'both'})


class ModelPoolCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=0, ge=0)
    capabilities: set[Capability] = Field(default_factory=lambda: {'text', 'responses'})
    min_context: int = Field(gt=0)
    enabled: bool = True


class ModelRoutingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(default=0, ge=0)
    groups: dict[str, list[ModelPoolCandidate]]
    free_user_daily_limit: int = Field(default=2, gt=0, le=2**31 - 1)
    free_account_daily_limit: int = Field(default=50, gt=0, le=2**31 - 1)
    free_account_minute_limit: int = Field(default=20, gt=0, le=2**31 - 1)

    @model_validator(mode='before')
    @classmethod
    def merge_legacy_free_groups(cls, value):
        if not isinstance(value, dict):
            return value
        raw_groups = value.get('groups')
        if not isinstance(raw_groups, dict):
            return value
        has_legacy = any(legacy_id in raw_groups for legacy_id in LEGACY_FREE_GROUP_IDS)
        if not has_legacy:
            return value
        merged_groups = {k: v for k, v in raw_groups.items() if k not in LEGACY_FREE_GROUP_IDS}
        combined: dict[tuple[str, str], dict] = {}
        sources = [raw_groups.get(FREE_GROUP_ID) or []] + [
            raw_groups.get(legacy_id) or []
            for legacy_id in ('agent-runtime-free-small', 'agent-runtime-free-medium', 'agent-runtime-free-large')
        ]
        for group_items in sources:
            if not isinstance(group_items, list):
                continue
            for item in group_items:
                candidate = item.model_dump() if isinstance(item, ModelPoolCandidate) else dict(item)
                key = (candidate.get('connection_id', ''), candidate.get('model_id', ''))
                existing = combined.get(key)
                if existing is None:
                    combined[key] = candidate
                else:
                    # Prefer enabled candidate and lower priority number when merging duplicates.
                    if (not existing.get('enabled', True) and candidate.get('enabled', True)) or (
                        existing.get('enabled', True) == candidate.get('enabled', True)
                        and candidate.get('priority', 0) < existing.get('priority', 0)
                    ):
                        combined[key] = candidate
        merged_groups[FREE_GROUP_ID] = list(combined.values())
        return {**value, 'groups': merged_groups}

    @model_validator(mode='after')
    def validate_groups(self):
        if set(self.groups) != set(GROUPS):
            raise ValueError('All four model groups are required')
        for candidates in self.groups.values():
            identities = {(candidate.connection_id, candidate.model_id) for candidate in candidates}
            if len(identities) != len(candidates):
                raise ValueError('Duplicate model pool candidate')
            if any('text' not in candidate.capabilities for candidate in candidates):
                raise ValueError('Each model pool candidate must support text')
        return self


@dataclass(frozen=True)
class Connection:
    id: str
    url: str
    key: str
    options: dict

    @property
    def is_openrouter(self) -> bool:
        return self.url.rstrip('/') == OPENROUTER_URL

    @property
    def enabled(self) -> bool:
        return self.options.get('enable', True) is not False

    @property
    def tier_scope(self) -> TierScope:
        return self.options.get('tier_scope') or 'both'

    def allows_tier(self, tier: str) -> bool:
        return self.tier_scope in (tier, 'both')


@dataclass(frozen=True)
class SelectedModel:
    group_id: str
    candidate: ModelPoolCandidate
    connection: Connection
    config: ModelRoutingConfig
    run_id: str | None

    @property
    def capability_overrides(self) -> dict:
        return {
            'tool_calling': 'tools' in self.candidate.capabilities,
            'context_window_tokens': self.candidate.min_context,
        }

    def dispatch(self, user_id: str, *, is_background_task: bool = False) -> DispatchContext:
        dispatch_key = uuid4().hex
        return DispatchContext(
            dispatch_key=dispatch_key,
            user_id=user_id,
            group_id=self.group_id,
            model_id=self.candidate.model_id,
            connection_id=self.connection.id,
            run_id=f'bg:{dispatch_key}' if is_background_task and self.run_id is None else self.run_id,
            free_account=(
                FREE_ACCOUNT
                if self.connection.is_openrouter
                and (
                    self.candidate.model_id == OPENROUTER_FREE_ROUTER_ID
                    or self.candidate.model_id.endswith(':free')
                )
                else None
            ),
            free_user_daily_limit=self.config.free_user_daily_limit,
            free_account_minute_limit=self.config.free_account_minute_limit,
            free_account_daily_limit=self.config.free_account_daily_limit,
            is_background_task=is_background_task,
        )

    def apply(self, payload: dict) -> None:
        payload['model'] = self.candidate.model_id
        # These selectors let an OpenRouter caller override the approved target.
        for key in ('models', 'route', 'provider', 'plugins', 'transforms', 'openrouter'):
            payload.pop(key, None)
        if GROUPS[self.group_id] == 'free':
            tools = payload.get('tools')
            if tools and (
                not isinstance(tools, list)
                or any(not isinstance(tool, dict) or tool.get('type') != 'function' for tool in tools)
            ):
                raise policy_error('model_unavailable', 400)
            if self.connection.is_openrouter:
                payload['provider'] = {
                    'max_price': {'prompt': 0, 'completion': 0, 'request': 0},
                    'data_collection': 'deny',
                }


def normalize_connections(urls: list[str], keys: list[str], configs: dict, previous=()) -> dict:
    """Allocate names at save time. Changing the URL, credential or transport invalidates approval."""
    previous = {connection.id: connection for connection in previous}
    normalized = {}
    ids = set()
    for index, base_url in enumerate(urls):
        url = base_url.rstrip('/')
        key = keys[index] if index < len(keys) else ''
        options = dict(configs.get(str(index), configs.get(base_url, {})))
        tier_scope = options.get('tier_scope')
        if tier_scope is None:
            tier_scope = 'both'
        elif not isinstance(tier_scope, str) or tier_scope not in VALID_TIER_SCOPES:
            raise policy_error('invalid_connection_tier', 422)
        options['tier_scope'] = tier_scope
        requested_id = options.pop('connection_id', None)
        old = previous.get(requested_id)
        identity = (url, key, options.get('api_type'), options.get('auth_type'), options.get('azure'))
        previous_identity = (
            (
                old.url,
                old.key,
                old.options.get('api_type'),
                old.options.get('auth_type'),
                old.options.get('azure'),
            )
            if old
            else None
        )
        if requested_id is not None and old is None:
            raise policy_error('invalid_connection_id', 422)
        # Deterministic IDs are used for existing unsaved connections only, so an
        # admin can configure a pool without first editing unrelated settings.
        if old:
            connection_id = old.id if identity == previous_identity else str(uuid4())
        elif previous:
            # Clients that omit an existing ID keep it only on an unambiguous match.
            matches = [
                item.id
                for item in previous.values()
                if (
                    item.url,
                    item.key,
                    item.options.get('api_type'),
                    item.options.get('auth_type'),
                    item.options.get('azure'),
                )
                == identity
            ]
            connection_id = matches[0] if len(matches) == 1 else str(uuid4())
        else:
            digest = hashlib.sha256(JSONCodec.dumps(identity).encode()).hexdigest()
            connection_id = str(uuid5(NAMESPACE_URL, f'agent-runtime:connection:{index}:{digest}'))
        if connection_id in ids:
            raise policy_error('invalid_connection_id', 422)
        ids.add(connection_id)
        options['connection_id'] = connection_id
        normalized[str(index)] = options
    return normalized


async def get_connections(*, include_disabled=False) -> dict[str, Connection]:
    values = await Config.get_many('openai.enable', 'openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    if values.get('openai.enable') is False and not include_disabled:
        return {}
    urls = values.get('openai.api_base_urls') or []
    keys = values.get('openai.api_keys') or []
    configs = values.get('openai.api_configs') or {}
    legacy = normalize_connections(
        urls,
        keys,
        {index: {k: v for k, v in config.items() if k != 'connection_id'} for index, config in configs.items()},
    )
    connections = {}
    duplicate_ids = set()
    for index, url in enumerate(urls):
        options = dict(configs.get(str(index), configs.get(url, {})))
        connection_id = options.get('connection_id') or legacy[str(index)]['connection_id']
        if connection_id in connections:
            duplicate_ids.add(connection_id)
        connections[connection_id] = Connection(
            connection_id,
            url.rstrip('/'),
            keys[index] if index < len(keys) else '',
            options,
        )
    return {key: connection for key, connection in connections.items() if key not in duplicate_ids}


async def get_model_routing_config() -> ModelRoutingConfig:
    stored = await Config.get(CONFIG_KEY)
    return (
        ModelRoutingConfig.model_validate(stored)
        if stored is not None
        else ModelRoutingConfig(groups={group: [] for group in GROUPS})
    )


_catalog: dict[str, dict] = {}
_catalog_expires_at = 0.0
_catalog_lock = asyncio.Lock()


async def _openrouter_catalog(connection: Connection, *, force_refresh: bool = False) -> dict[str, dict]:
    global _catalog, _catalog_expires_at
    if not force_refresh and time.monotonic() < _catalog_expires_at:
        return _catalog
    async with _catalog_lock:
        if not force_refresh and time.monotonic() < _catalog_expires_at:
            return _catalog
        from weai.utils.session_pool import get_session

        try:
            session = await get_session()
            async with session.get(
                f'{OPENROUTER_URL}/models',
                timeout=ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
                headers={'Authorization': f'Bearer {connection.key}'},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return {}
                body = await response.json()
                _catalog = {entry['id']: entry for entry in body['data']}
                _catalog_expires_at = time.monotonic() + 3600
                return _catalog
        except (ClientError, OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
            return {}


async def list_free_catalog_models(*, refresh: bool = False) -> list[dict]:
    """List all `:free` models from enabled Free-tier connections for admin checklist setup."""
    from weai.utils.session_pool import get_session

    connections = await get_connections()
    items: list[dict] = []
    for connection in connections.values():
        if not _usable(connection) or not connection.allows_tier('free'):
            continue
        host = urlsplit(connection.url).netloc.rsplit('@', 1)[-1]
        if connection.is_openrouter:
            catalog = await _openrouter_catalog(connection, force_refresh=refresh)
            for model_id, entry in catalog.items():
                if not is_free_model(model_id, entry):
                    continue
                caps = ['text']
                if catalog_supports(entry, {'image'}):
                    caps.append('image')
                if catalog_supports(entry, {'tools'}):
                    caps.append('tools')
                if catalog_supports(entry, {'structured_output'}):
                    caps.append('structured_output')
                if connection.options.get('api_type') == 'responses':
                    caps.append('responses')
                raw_ctx = entry.get('context_length')
                context_length = int(raw_ctx) if isinstance(raw_ctx, (int, float)) and raw_ctx > 0 else 8192
                items.append(
                    {
                        'connection_id': connection.id,
                        'connection_url': host,
                        'model_id': model_id,
                        'name': entry.get('name') or model_id,
                        'context_length': context_length,
                        'capabilities': caps,
                    }
                )
        else:
            try:
                session = await get_session()
                async with session.get(
                    f'{connection.url.rstrip("/")}/models',
                    timeout=ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
                    headers={'Authorization': f'Bearer {connection.key}'},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        continue
                    body = await response.json()
                    for entry in body.get('data') or []:
                        model_id = entry.get('id') if isinstance(entry, dict) else None
                        if (
                            not isinstance(model_id, str)
                            or ':free' not in model_id
                            or not is_text_generation_model(entry)
                        ):
                            continue
                        raw_ctx = entry.get('context_length')
                        context_length = int(raw_ctx) if isinstance(raw_ctx, (int, float)) and raw_ctx > 0 else 8192
                        items.append(
                            {
                                'connection_id': connection.id,
                                'connection_url': host,
                                'model_id': model_id,
                                'name': entry.get('name') or model_id,
                                'context_length': context_length,
                                'capabilities': (
                                    ['text', 'responses']
                                    if connection.options.get('api_type') == 'responses'
                                    else ['text']
                                ),
                            }
                        )
            except (ClientError, OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
                continue
    items.sort(
        key=lambda item: (
            0 if item['model_id'] == OPENROUTER_FREE_ROUTER_ID else 1,
            item['model_id'].lower(),
            item['connection_id'],
        )
    )
    return items


def is_free_model(model_id: str, entry: dict) -> bool:
    if model_id != OPENROUTER_FREE_ROUTER_ID and not model_id.endswith(':free'):
        return False
    if not is_text_generation_model({**(entry if isinstance(entry, dict) else {}), 'id': model_id}):
        return False
    pricing = entry.get('pricing') or {}
    try:
        # The OpenRouter catalog omits optional surcharges when they are
        # zero. Prompt and completion prices must always be present.
        required = {'prompt', 'completion'}
        return required <= pricing.keys() and all(Decimal(str(value)) == 0 for value in pricing.values())
    except (AttributeError, TypeError, InvalidOperation):
        return False


def catalog_supports(entry: dict, requirements: set[str]) -> bool:
    modalities = (entry.get('architecture') or {}).get('input_modalities') or []
    parameters = entry.get('supported_parameters') or []
    return (
        ('image' not in requirements or 'image' in modalities)
        and ('tools' not in requirements or 'tools' in parameters)
        and ('structured_output' not in requirements or 'structured_outputs' in parameters)
    )


def _usable(connection: Connection) -> bool:
    target = urlsplit(connection.url)
    return (
        connection.enabled
        and bool(connection.key)
        and target.scheme == 'https'
        and bool(target.hostname)
        and target.username is None
        and not target.query
        and not target.fragment
        and not connection.options.get('azure')
        and not connection.options.get('headers')
        and connection.options.get('auth_type') in {None, 'bearer'}
    )


async def update_model_routing_config(config: ModelRoutingConfig) -> ModelRoutingConfig:
    connections = await get_connections()
    for group_id, candidates in config.groups.items():
        for candidate in candidates:
            if not candidate.enabled:
                continue
            connection = connections.get(candidate.connection_id)
            if not connection or not _usable(connection) or not connection.allows_tier(GROUPS[group_id]):
                raise policy_error('invalid_model_connection', 422)
            if GROUPS[group_id] == 'free' and connection.is_openrouter:
                if not is_free_model(
                    candidate.model_id, (await _openrouter_catalog(connection)).get(candidate.model_id, {})
                ):
                    raise policy_error('invalid_free_model', 422)
    async with get_async_db() as db, db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {'key': CONFIG_KEY})
        current = await db.get(Config, CONFIG_KEY)
        version = current.value['version'] if current else 0
        if version != config.version:
            raise policy_error('model_pool_changed', 409)
        updated = config.model_copy(update={'version': version + 1})
        await Config.upsert({CONFIG_KEY: updated.model_dump(mode='json')}, db=db)
    return updated


def request_requirements(payload: dict, *, responses: bool = False) -> tuple[set[str], int]:
    from weai.agent_runtime.context_budget import estimate_tokens

    capabilities = {'text'}
    if responses:
        capabilities.add('responses')
    if payload.get('tools') or payload.get('functions'):
        capabilities.add('tools')
    response_format = payload.get('response_format')
    if (isinstance(response_format, dict) and response_format.get('type') in {'json_schema', 'json_object'}) or (
        isinstance(payload.get('text'), dict) and payload['text'].get('format')
    ):
        capabilities.add('structured_output')
    messages = payload.get('messages') or payload.get('input') or []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get('type') in {'image_url', 'input_image'} for part in content
        ):
            capabilities.add('image')
    output_tokens = (
        payload.get('max_completion_tokens') or payload.get('max_output_tokens') or payload.get('max_tokens')
    )
    serializable_payload = {k: v for k, v in payload.items() if k != 'metadata'}
    return capabilities, estimate_tokens(JSONCodec.dumps(serializable_payload), '') + (
        output_tokens if type(output_tokens) is int else 0
    )


async def select_model(
    user_id,
    group_id,
    payload,
    run_id=None,
    *,
    responses=False,
    exclude=None,
) -> SelectedModel:
    group_id = normalize_group_id(group_id)
    if not group_id or group_id not in GROUPS:
        raise policy_error('entitlement_missing', 403)
    config = await get_model_routing_config()
    connections = await get_connections()
    requirements, tokens = request_requirements(payload, responses=responses)
    pinned = None
    if run_id and exclude is None:
        async with get_async_db() as db:
            pinned = (
                await db.execute(
                    select(selections).where(
                        selections.c.user_id == user_id,
                        selections.c.run_id == run_id,
                    )
                )
            ).one_or_none()
        if pinned and pinned.group_id != group_id:
            raise policy_error('model_unavailable', 503)
    # Balance calls between equivalent Free candidates without changing a pinned run.
    selection_seed = run_id or uuid4().hex
    candidates = sorted(
        config.groups[group_id],
        key=lambda item: (
            item.priority,
            (
                hashlib.sha256(f'{selection_seed}:{item.connection_id}:{item.model_id}'.encode()).hexdigest()
                if GROUPS[group_id] == 'free'
                else item.connection_id
            ),
            item.model_id,
        ),
    )
    excluded_ids: set[tuple[str, str]] = set()
    if exclude is not None:
        if (
            isinstance(exclude, tuple)
            and len(exclude) == 2
            and isinstance(exclude[0], str)
            and isinstance(exclude[1], str)
        ):
            excluded_ids.add(exclude)
        else:
            excluded_ids.update(exclude)

    selected = None
    for candidate in candidates:
        if (candidate.connection_id, candidate.model_id) in excluded_ids:
            continue
        if not candidate.enabled or not requirements <= candidate.capabilities or candidate.min_context < tokens:
            continue
        if pinned and (candidate.connection_id, candidate.model_id) != (pinned.connection_id, pinned.model_id):
            continue
        connection = connections.get(candidate.connection_id)
        if not connection or not _usable(connection) or not connection.allows_tier(GROUPS[group_id]):
            continue
        if connection.options.get('api_type') == 'responses' and 'responses' not in candidate.capabilities:
            continue
        if GROUPS[group_id] == 'free' and connection.is_openrouter:
            metadata = (await _openrouter_catalog(connection)).get(candidate.model_id, {})
            if not is_free_model(candidate.model_id, metadata) or (metadata.get('context_length') or 0) < tokens:
                continue
            if candidate.model_id != OPENROUTER_FREE_ROUTER_ID and not catalog_supports(metadata, requirements):
                continue
        if not pinned and await tier_repository.recently_rate_limited(connection.id, candidate.model_id):
            continue
        selected = SelectedModel(group_id, candidate, connection, config, run_id)
        break
    if not selected:
        raise policy_error('model_unavailable', 503)
    if run_id and not pinned and exclude is None:
        async with get_async_db() as db, db.begin():
            await db.execute(
                insert(selections)
                .values(
                    user_id=user_id,
                    run_id=run_id,
                    group_id=group_id,
                    connection_id=selected.connection.id,
                    model_id=selected.candidate.model_id,
                    pool_version=config.version,
                )
                .on_conflict_do_nothing(index_elements=[selections.c.user_id, selections.c.run_id])
            )
            winner = (
                await db.execute(
                    select(selections).where(
                        selections.c.user_id == user_id,
                        selections.c.run_id == run_id,
                    )
                )
            ).one()
            if (winner.group_id, winner.connection_id, winner.model_id) != (
                group_id,
                selected.connection.id,
                selected.candidate.model_id,
            ):
                raise policy_error('model_unavailable', 503)
    return selected


def compute_rate_limit_retry_delay(
    attempt: int,
    retry_after_header: str | None = None,
    remaining_budget: float = FREE_429_MAX_TOTAL_WAIT_SECONDS,
) -> float | None:
    """Compute bounded backoff delay (seconds) for same-model 429 retry."""
    if remaining_budget <= 0:
        return None
    base_delay = FREE_429_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt))
    jitter = random.uniform(0.0, 0.25)
    delay = base_delay + jitter
    if retry_after_header is not None:
        try:
            parsed = float(str(retry_after_header).strip())
            if parsed > 0:
                delay = max(delay, parsed)
        except ValueError:
            pass
    capped = min(delay, FREE_429_MAX_SINGLE_WAIT_SECONDS, remaining_budget)
    return capped if capped > 0 else None


async def select_rate_limit_fallback(
    user_id,
    request_payload,
    previous: SelectedModel,
    run_attempt,
    *,
    excluded=None,
):
    from weai.agent_runtime.models import AgentEvent, AgentSession, AgentToolInvocation, AgentTurn

    previous_id = (previous.connection.id, previous.candidate.model_id)
    excluded_ids = set(excluded or ()) | {previous_id}
    try:
        replacement = await select_model(
            user_id,
            previous.group_id,
            request_payload,
            previous.run_id,
            exclude=excluded_ids,
        )
    except HTTPException as error:
        if error.status_code != 503:
            raise
        return None
    if previous.run_id is None:
        return replacement
    if run_attempt is None:
        return None

    # The runtime appends events under the turn lock. Only a running attempt
    # with confirmed rate-limited dispatches and no output or tool operations
    # can replace its pinned provider.
    max_prior = FREE_429_MAX_FALLBACKS if GROUPS.get(previous.group_id) == 'free' else 1
    async with get_async_db() as db, db.begin():
        current_attempt = await db.scalar(
            select(AgentTurn.attempts)
            .join(AgentSession)
            .where(
                AgentTurn.id == previous.run_id,
                AgentSession.owner_id == user_id,
                AgentTurn.status == 'running',
                AgentTurn.cancel_requested.is_(False),
                AgentTurn.lease_until > func.now(),
            )
            .with_for_update(of=AgentTurn)
        )
        if current_attempt != run_attempt:
            return None
        if await db.scalar(
            select(AgentEvent.id)
            .where(
                AgentEvent.turn_id == previous.run_id,
                AgentEvent.event_type.like('client.%'),
            )
            .limit(1)
        ):
            return None
        if await db.scalar(
            select(AgentToolInvocation.id)
            .where(
                AgentToolInvocation.turn_id == previous.run_id,
            )
            .limit(1)
        ):
            return None
        prior_calls = (
            (
                await db.execute(
                    select(dispatches.c.provider_status)
                    .where(
                        dispatches.c.user_id == user_id,
                        dispatches.c.run_id == previous.run_id,
                    )
                    .limit(max_prior + 1)
                )
            )
            .scalars()
            .all()
        )
        allowed_prior_statuses = {429, 404, 502, 503} if GROUPS.get(previous.group_id) == 'free' else {429}
        if not prior_calls or len(prior_calls) > max_prior or any(status not in allowed_prior_statuses for status in prior_calls):
            return None
        changed = await db.scalar(
            update(selections)
            .where(
                selections.c.user_id == user_id,
                selections.c.run_id == previous.run_id,
                selections.c.group_id == previous.group_id,
                selections.c.connection_id == previous_id[0],
                selections.c.model_id == previous_id[1],
            )
            .values(
                connection_id=replacement.connection.id,
                model_id=replacement.candidate.model_id,
                pool_version=replacement.config.version,
            )
            .returning(selections.c.run_id)
        )
        return replacement if changed else None
