import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.model_routing.tier_service import DispatchContext, TierRepository

NOW = datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def tier_db():
    from weai.persistence.v2.migrations.runner import run_v2_migrations

    database_url = os.getenv('AGENT_RUNTIME_TEST_POSTGRES_URL')
    if not database_url:
        pytest.skip('Set AGENT_RUNTIME_TEST_POSTGRES_URL to a disposable PostgreSQL database')
    url = make_url(database_url)
    if url.get_backend_name() != 'postgresql':
        pytest.fail('AGENT_RUNTIME_TEST_POSTGRES_URL must be PostgreSQL')
    url = url.set(drivername='postgresql+psycopg')
    schema = f'ar_test_tier_{uuid4().hex}'
    admin_engine = create_async_engine(url)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict({'options': f'-csearch_path={schema}'})
    engine = create_async_engine(scoped_url)
    try:
        await asyncio.to_thread(run_v2_migrations, scoped_url.render_as_string(hide_password=False), 'head')
        sessionmaker = async_sessionmaker(engine)
        async with sessionmaker.begin() as db:
            await db.execute(text("""
                INSERT INTO ar_ow_user (id, email, role, name)
                VALUES ('u', 'test@example.com', 'user', 'Test'),
                       ('other', 'other@example.com', 'user', 'Other'),
                       ('pending', 'pending@example.com', 'pending', 'Pending')
            """))
            await db.execute(text('INSERT INTO ar_ow_auth (id, email, active) SELECT id, email, true FROM ar_ow_user'))
        yield TierRepository(sessionmaker), sessionmaker
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


def attempt(key, user='u', group='agent-runtime-plus-small', **kwargs):
    return DispatchContext(key, user, group, 'private-model', 'test-connection', **kwargs)


@pytest.mark.asyncio
async def test_default_free_and_pending_denied(tier_db):
    repo, _ = tier_db
    assert (await repo.get_plan('u')).tier == 'free'
    assert (await repo.get_usage('u', NOW, 2)).remaining == 2
    with pytest.raises(HTTPException) as failure:
        await repo.get_plan('pending')
    assert failure.value.status_code == 403


@pytest.mark.asyncio
async def test_last_plus_call_is_atomic_and_unknown_consumes(tier_db):
    repo, dbs = tier_db
    await repo.set_plan('u', 'plus', 1, 'admin')
    results = await asyncio.gather(
        repo.begin_dispatch(attempt('one'), 'fingerprint-1', NOW),
        repo.begin_dispatch(attempt('two'), 'fingerprint-2', NOW),
        return_exceptions=True,
    )
    assert results.count(None) == 1
    failure = next(result for result in results if result is not None)
    assert failure.detail['code'] == 'quota_exhausted'
    assert failure.headers['Retry-After']
    async with dbs() as db:
        key = await db.scalar(text('SELECT dispatch_key FROM ar_llm_dispatch'))
    with pytest.raises(HTTPException) as duplicate:
        await repo.begin_dispatch(attempt(key), 'different fingerprint', NOW)
    assert duplicate.value.status_code == 409
    # A newly constructed repository observes the same durable outcome.
    new_repo = TierRepository(dbs)
    await new_repo.finish_dispatch(key)
    usage = await new_repo.get_usage('u', NOW, 2)
    assert (usage.used, usage.remaining) == (1, 0)
    assert usage.reset_at == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert (await new_repo.get_usage('u', usage.reset_at, 2)).remaining == 1


@pytest.mark.asyncio
async def test_free_shared_capacity_is_atomic_between_users(tier_db):
    repo, _ = tier_db
    limits = dict(free_account='openrouter', free_account_minute_limit=1)
    results = await asyncio.gather(
        repo.begin_dispatch(attempt('one', group='agent-runtime-free-small', **limits), 'one', NOW),
        repo.begin_dispatch(attempt('two', user='other', group='agent-runtime-free-medium', **limits), 'two', NOW),
        return_exceptions=True,
    )
    assert results.count(None) == 1
    failure = next(result for result in results if result is not None)
    assert failure.detail['code'] == 'free_capacity_exhausted'
    assert failure.detail['reset_at'] == '2026-12-01T00:00:00+00:00'


@pytest.mark.asyncio
async def test_downgrade_blocks_preselected_plus_model(tier_db):
    repo, _ = tier_db
    await repo.set_plan('u', 'plus', 4, 'admin')
    await repo.set_plan('u', 'free', None, 'admin')
    with pytest.raises(HTTPException) as failure:
        await repo.begin_dispatch(attempt('forbidden'), 'fingerprint', NOW)
    assert failure.value.detail['code'] == 'entitlement_missing'


@pytest.mark.asyncio
async def test_upstream_free_limit_holds_other_groups_until_reset(tier_db):
    from datetime import timedelta

    repo, _ = tier_db
    reset = NOW + timedelta(hours=1)
    await repo.begin_dispatch(attempt('sent', group='agent-runtime-free-small', free_account='openrouter'), 'sent', NOW)
    await repo.finish_dispatch('sent', status=429, shared_reset_at=reset)
    other = attempt('other-group', user='other', group='agent-runtime-free-medium', free_account='openrouter')
    with pytest.raises(HTTPException) as failure:
        await repo.begin_dispatch(other, 'other', NOW)
    assert failure.value.detail['reset_at'] == reset.isoformat()
    await repo.begin_dispatch(other, 'other', reset)


@pytest.mark.asyncio
async def test_approved_provider_dispatches_share_usage_and_reject_bypass(tier_db, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from weai.model_routing import model_routing, tier_routes, tier_service
    from weai.routers import openai
    from weai.utils.json_codec import JSONCodec

    repo, _ = tier_db
    await repo.set_plan('u', 'plus', 2, 'admin')
    monkeypatch.setattr(tier_service, 'tier_repository', repo)
    monkeypatch.setattr(tier_routes, 'tier_repository', repo)
    monkeypatch.setattr(model_routing, 'tier_repository', repo)
    first = model_routing.Connection('first', 'https://api.openai.com/v1', 'first-key', {})
    second = model_routing.Connection('second', 'https://api.1min.ai/openai/v1', 'second-key', {})
    groups = {group: [] for group in tier_service.GROUPS}
    groups['agent-runtime-plus-small'] = [
        model_routing.ModelPoolCandidate(connection_id='first', model_id='private-small', min_context=8000),
    ]
    groups['agent-runtime-plus-medium'] = [
        model_routing.ModelPoolCandidate(
            connection_id='second',
            model_id='private-medium',
            min_context=8000,
            capabilities={'text', 'responses'},
        ),
    ]
    config = model_routing.ModelRoutingConfig(groups=groups)
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'first': first, 'second': second}))
    monkeypatch.setattr(openai.Config, 'get', AsyncMock(return_value=True))

    stream_body = (
        b'data: {"model":"private-small","provider":"internal","choices":[{"delta":{"content":"OK"}}]}\n\n'
        b'data: [DONE]\n\n'
    )

    async def stream_chunks():
        yield stream_body[:49], True
        yield stream_body[49:], True

    async def transport(method, url, **options):
        assert options['allow_redirects'] is False
        payload = JSONCodec.loads(options['data'])
        if url.endswith('/chat/completions'):
            assert payload['model'] == 'private-small'
            return SimpleNamespace(
                status=200,
                closed=True,
                headers={'Content-Type': 'text/event-stream'},
                content=SimpleNamespace(iter_chunks=stream_chunks),
            )
        assert payload['model'] == 'private-medium'
        return SimpleNamespace(
            status=200,
            closed=True,
            headers={},
            json=AsyncMock(return_value={'model': 'private-medium', 'provider': 'internal', 'output': []}),
        )

    session = SimpleNamespace(request=AsyncMock(side_effect=transport))
    monkeypatch.setattr(openai, 'get_session', AsyncMock(return_value=session))
    monkeypatch.setattr(openai, 'ENABLE_OPENAI_API_PASSTHROUGH', True)
    app = FastAPI()
    app.include_router(openai.router, prefix='/openai')
    app.include_router(tier_routes.router, prefix='/api/v1/agent-runtime')
    monkeypatch.setattr(tier_routes, 'get_model_routing_config', AsyncMock(return_value=config))

    def require_admin():
        raise HTTPException(403)

    app.dependency_overrides[tier_routes.get_admin_user] = require_admin
    app.dependency_overrides[openai.get_verified_user] = lambda: SimpleNamespace(id='u', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        denied_plan_change = await client.put(
            '/api/v1/agent-runtime/admin/users/other/plan',
            json={'tier': 'plus', 'monthly_request_limit': 4},
        )
        assert denied_plan_change.status_code == 403
        catalog = (await client.get('/openai/models')).json()['data']
        assert [model['id'] for model in catalog] == ['agent-runtime-plus-small', 'agent-runtime-plus-medium', 'agent-runtime-plus-large']
        assert all(model['owned_by'] == 'agent-runtime' for model in catalog)
        assert (await client.post('/openai/chat/completions', json={'model': 'private-small'})).status_code == 403
        assert (await client.post('/openai/responses', json={'model': 'agent-runtime-free-small'})).status_code == 403
        assert (await client.post('/openai/other-inference', json={'model': 'private-small'})).status_code == 403
        messages = [{'role': 'user', 'content': 'Hi'}]
        chat = await client.post(
            '/openai/chat/completions',
            json={
                'model': 'agent-runtime-plus-small',
                'messages': messages,
                'stream': True,
            },
        )
        assert chat.status_code == 200
        assert 'agent-runtime-plus-small' in chat.text
        assert 'private-small' not in chat.text and 'internal' not in chat.text
        assert 'data: [DONE]' in chat.text

        responses = await client.post('/openai/responses', json={'model': 'agent-runtime-plus-medium', 'input': 'Hi'})
        assert responses.status_code == 200 and responses.json()['model'] == 'agent-runtime-plus-medium'
        exhausted = await client.post('/openai/chat/completions', json={'model': 'agent-runtime-plus-small', 'messages': messages})
        assert exhausted.status_code == 429
        assert exhausted.json()['detail']['code'] == 'quota_exhausted'
        allowance = await client.get('/api/v1/agent-runtime/me/plan')
        assert allowance.status_code == 200 and allowance.json()['remaining'] == 0
    assert session.request.await_count == 2
    usage = await repo.get_usage('u', datetime.now(timezone.utc), 2)
    assert (usage.used, usage.remaining) == (2, 0)


@pytest.mark.asyncio
async def test_gateway_never_charges_a_rejected_final_body(tier_db, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from weai.agent_runtime.context import PreparedContext
    from weai.agent_runtime.gateway import ProviderGateway
    from weai.model_routing import tier_service

    repo, _ = tier_db
    await repo.set_plan('u', 'plus', 1, 'admin')
    monkeypatch.setattr(tier_service, 'tier_repository', repo)
    gateway = ProviderGateway()
    session = SimpleNamespace(request=AsyncMock())
    prepared = PreparedContext(payload={}, wire_body='{"value":"' + 'x' * 100_001 + '"}', metrics={})
    with pytest.raises(HTTPException) as failure:
        await gateway.send(
            session, 'POST', 'https://api.openai.com/v1/chat/completions', prepared, llm_dispatch=attempt('oversized')
        )
    assert failure.value.status_code == 413
    session.request.assert_not_awaited()
    assert (await repo.get_usage('u', datetime.now(timezone.utc), 2)).remaining == 1


@pytest.mark.asyncio
async def test_first_admin_bootstrap_is_atomic(tier_db, monkeypatch):
    from contextlib import asynccontextmanager

    from weai.models import auths

    _, dbs = tier_db
    async with dbs.begin() as db:
        await db.execute(text('DELETE FROM ar_ow_auth'))
        await db.execute(text('DELETE FROM ar_ow_user'))

    @asynccontextmanager
    async def db_context(_db=None):
        async with dbs() as session:
            yield session

    monkeypatch.setattr(auths, 'get_async_db_context', db_context)
    results = await asyncio.gather(
        *[
            auths.Auths.insert_new_auth(email, 'unused-password-hash', 'Test', bootstrap_only=True)
            for email in ('one@example.com', 'two@example.com')
        ]
    )
    assert sum(result is not None for result in results) == 1
    async with dbs() as db:
        assert (
            await db.execute(text('SELECT u.role, a.active FROM ar_ow_user u JOIN ar_ow_auth a ON a.id = u.id'))
        ).one() == ('admin', True)


@pytest.mark.asyncio
async def test_rate_limit_fallback_replaces_only_pristine_run_selection(tier_db, monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from weai.agent_runtime.models import AgentEvent, AgentSession, AgentTurn
    from weai.model_routing import model_routing, tier_service

    repo, dbs = tier_db
    await repo.set_plan('u', 'plus', 12, 'admin')

    @asynccontextmanager
    async def db_context():
        async with dbs() as db:
            yield db

    monkeypatch.setattr(model_routing, 'get_async_db', db_context)
    monkeypatch.setattr(model_routing, 'tier_repository', repo)
    connections = {
        'first': model_routing.Connection('first', 'https://api.openai.com/v1', 'first-key', {}),
        'second': model_routing.Connection('second', 'https://api.openai.com/v1', 'second-key', {}),
    }
    group = 'agent-runtime-plus-small'
    groups = {item: [] for item in tier_service.GROUPS}
    groups[group] = [
        model_routing.ModelPoolCandidate(
            connection_id=connection_id,
            model_id=f'private-{connection_id}',
            min_context=8000,
        )
        for connection_id in connections
    ]
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value=connections))
    monkeypatch.setattr(
        model_routing,
        'get_model_routing_config',
        AsyncMock(
            return_value=model_routing.ModelRoutingConfig(groups=groups),
        ),
    )
    payload = {'model': group, 'messages': [{'role': 'user', 'content': 'Hi'}]}
    expired = datetime.now(timezone.utc) + timedelta(minutes=5)

    async def run_with_rate_limit(run_id, sequence):
        async with dbs.begin() as db:
            if sequence == 1:
                db.add(AgentSession(id='session', owner_id='u'))
                await db.flush()
            db.add(
                AgentTurn(
                    id=run_id,
                    session_id='session',
                    sequence=sequence,
                    status='running',
                    request_payload={},
                    engine_run_id=run_id,
                    engine_thread_id='session',
                    attempts=1,
                    lease_until=expired,
                )
            )
        initial = await model_routing.select_model('u', group, payload, run_id)
        dispatch = initial.dispatch('u')
        await repo.begin_dispatch(dispatch, 'fingerprint', NOW)
        await repo.finish_dispatch(dispatch.dispatch_key, status=429)
        return initial

    initial = await run_with_rate_limit('first-turn', 1)
    replacement = await model_routing.select_rate_limit_fallback('u', payload, initial, 1)
    assert replacement.connection.id != initial.connection.id
    pinned = await model_routing.select_model('u', group, payload, 'first-turn')
    assert pinned.connection.id == replacement.connection.id

    initial = await run_with_rate_limit('second-turn', 2)
    async with dbs.begin() as db:
        db.add(
            AgentEvent(
                id='client-event',
                session_id='session',
                turn_id='second-turn',
                sequence=1,
                event_type='client.chat:completion',
                payload={},
            )
        )
    assert await model_routing.select_rate_limit_fallback('u', payload, initial, 1) is None
    pinned = await model_routing.select_model('u', group, payload, 'second-turn')
    assert pinned.connection.id == initial.connection.id
