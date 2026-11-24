from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from weai.model_routing import model_routing, tier_service
from weai.routers import openai as openai_router


def _make_selection(connection_id: str, model_id: str, *, group_id: str = 'agent-runtime-free'):
    connection = model_routing.Connection(
        connection_id,
        'https://example-free.test/v1',
        'test-key',
        {'tier_scope': 'free'},
    )
    candidate = model_routing.ModelPoolCandidate(
        connection_id=connection_id,
        model_id=model_id,
        min_context=8000,
    )
    config = model_routing.ModelRoutingConfig(
        groups={g: ([candidate] if g == group_id else []) for g in tier_service.GROUPS}
    )
    return model_routing.SelectedModel(group_id, candidate, connection, config, run_id=None)


def test_compute_rate_limit_retry_delay_respects_backoff_retry_after_and_caps(monkeypatch):
    monkeypatch.setattr(model_routing.random, 'uniform', lambda a, b: 0.1)

    assert model_routing.compute_rate_limit_retry_delay(0, None, 8.0) == pytest.approx(1.1)
    assert model_routing.compute_rate_limit_retry_delay(1, None, 8.0) == pytest.approx(2.1)
    # Respects Retry-After up to single-wait cap (5s)
    assert model_routing.compute_rate_limit_retry_delay(0, '3', 8.0) == pytest.approx(3.0)
    assert model_routing.compute_rate_limit_retry_delay(0, '30', 8.0) == pytest.approx(5.0)
    # Respects remaining total budget
    assert model_routing.compute_rate_limit_retry_delay(1, '4', 1.5) == pytest.approx(1.5)
    # Exhausted budget returns None
    assert model_routing.compute_rate_limit_retry_delay(0, None, 0.0) is None
    assert model_routing.compute_rate_limit_retry_delay(0, None, -1.0) is None


@pytest.mark.asyncio
async def test_select_model_excludes_collection_of_tried_candidates(monkeypatch):
    connection = model_routing.Connection('c1', 'https://example-free.test/v1', 'key', {'tier_scope': 'free'})
    candidates = [
        model_routing.ModelPoolCandidate(connection_id='c1', model_id=f'm{i}', priority=i, min_context=8000)
        for i in range(4)
    ]
    config = model_routing.ModelRoutingConfig(
        groups={g: (candidates if g == 'agent-runtime-free' else []) for g in tier_service.GROUPS}
    )
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'c1': connection}))
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))

    selected = await model_routing.select_model(
        'u1',
        'agent-runtime-free',
        {'messages': [{'role': 'user', 'content': 'hi'}]},
        exclude={('c1', 'm0'), ('c1', 'm1')},
    )
    assert selected.candidate.model_id == 'm2'


@pytest.mark.asyncio
async def test_free_429_falls_back_across_up_to_three_models(monkeypatch):
    selections = [_make_selection('c1', f'm{i}') for i in range(4)]
    calls: list[str] = []

    async def fake_generate(request, form_data, user, *, replacement=None, allow_rate_limit_retry=False):
        assert allow_rate_limit_retry is True
        current = replacement or selections[0]
        calls.append(current.candidate.model_id)
        if current.candidate.model_id != 'm3':
            err = HTTPException(429, detail={'code': 'provider_rate_limit', 'message': 'busy'})
            raise openai_router._CandidateRateLimited(err, current, run_attempt=None)
        return {'id': 'ok', 'model': current.candidate.model_id}

    async def fake_fallback(user_id, form_data, previous, run_attempt, *, excluded=None):
        excluded = set(excluded or ()) | {(previous.connection.id, previous.candidate.model_id)}
        for item in selections:
            if (item.connection.id, item.candidate.model_id) not in excluded:
                return item
        return None

    sleep_mock = AsyncMock()
    monkeypatch.setattr(openai_router, '_generate_chat_completion', fake_generate)
    monkeypatch.setattr(openai_router, 'select_rate_limit_fallback', fake_fallback)
    monkeypatch.setattr(openai_router.asyncio, 'sleep', sleep_mock)

    result = await openai_router.generate_chat_completion(
        request=SimpleNamespace(),
        form_data={'model': 'agent-runtime-free-small', 'messages': [{'role': 'user', 'content': 'hi'}]},
        user=SimpleNamespace(id='u1', role='user'),
    )
    assert result == {'id': 'ok', 'model': 'm3'}
    assert calls == ['m0', 'm1', 'm2', 'm3']
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_free_429_retries_same_model_with_backoff_when_no_fallback(monkeypatch):
    only = _make_selection('c1', 'm0')
    attempts = 0
    sleeps: list[float] = []

    async def fake_generate(request, form_data, user, *, replacement=None, allow_rate_limit_retry=False):
        nonlocal attempts
        attempts += 1
        current = replacement or only
        if attempts < 3:
            err = HTTPException(429, detail={'code': 'provider_rate_limit', 'message': 'busy'})
            raise openai_router._CandidateRateLimited(err, current, run_attempt=None, retry_after='1.5')
        return {'id': 'ok', 'attempts': attempts}

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(openai_router, '_generate_chat_completion', fake_generate)
    monkeypatch.setattr(openai_router, 'select_rate_limit_fallback', AsyncMock(return_value=None))
    monkeypatch.setattr(openai_router.asyncio, 'sleep', fake_sleep)
    monkeypatch.setattr(model_routing.random, 'uniform', lambda a, b: 0.0)

    result = await openai_router.generate_chat_completion(
        request=SimpleNamespace(),
        form_data={'model': 'agent-runtime-free-small', 'messages': [{'role': 'user', 'content': 'hi'}]},
        user=SimpleNamespace(id='u1', role='user'),
    )
    assert result == {'id': 'ok', 'attempts': 3}
    assert sleeps == [pytest.approx(1.5), pytest.approx(2.0)]


@pytest.mark.asyncio
async def test_free_429_fails_fast_on_free_capacity_exhausted(monkeypatch):
    calls = 0

    async def fake_generate(request, form_data, user, *, replacement=None, allow_rate_limit_retry=False):
        nonlocal calls
        calls += 1
        raise HTTPException(429, detail={'code': 'free_capacity_exhausted', 'message': 'exhausted'})

    fallback_mock = AsyncMock()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(openai_router, '_generate_chat_completion', fake_generate)
    monkeypatch.setattr(openai_router, 'select_rate_limit_fallback', fallback_mock)
    monkeypatch.setattr(openai_router.asyncio, 'sleep', sleep_mock)

    with pytest.raises(HTTPException) as exc_info:
        await openai_router.generate_chat_completion(
            request=SimpleNamespace(),
            form_data={'model': 'agent-runtime-free-small', 'messages': [{'role': 'user', 'content': 'hi'}]},
            user=SimpleNamespace(id='u1', role='user'),
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail['code'] == 'free_capacity_exhausted'
    assert calls == 1
    fallback_mock.assert_not_awaited()
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_count_query_excludes_upstream_429():
    captured_sql = []

    class FakeDB:
        async def scalar(self, stmt):
            captured_sql.append(str(stmt.compile(compile_kwargs={'literal_binds': True})))
            return 1

    repo = tier_service.TierRepository()
    from datetime import datetime, timezone

    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    count = await repo._user_count(FakeDB(), 'u1', 'free', now, now)
    assert count == 1
    assert captured_sql and 'IS DISTINCT FROM 429' in captured_sql[0].upper()
    assert 'DISTINCT' in captured_sql[0].upper()
    assert 'COALESCE' in captured_sql[0].upper()
    assert "BG:%" in captured_sql[0].upper()


def test_background_task_dispatch_tagged_as_bg():
    sel = _make_selection('c1', 'm0')
    ctx = sel.dispatch('u1', is_background_task=True)
    assert ctx.is_background_task is True
    assert ctx.run_id is not None and ctx.run_id.startswith('bg:')

    normal = sel.dispatch('u1', is_background_task=False)
    assert normal.is_background_task is False
    assert normal.run_id is None


def test_request_requirements_ignores_non_serializable_metadata():
    payload = {
        'model': 'agent-runtime-free-medium',
        'messages': [{'role': 'user', 'content': 'hello'}],
        'metadata': {'event_emitter': lambda event: event},
    }
    caps, tokens = model_routing.request_requirements(payload)
    assert 'text' in caps
    assert tokens > 0


@pytest.mark.asyncio
async def test_free_404_falls_back_without_same_model_retry(monkeypatch):
    s0 = _make_selection('c1', 'm0')
    s1 = _make_selection('c1', 'm1')
    calls: list[str] = []

    async def fake_generate(request, form_data, user, *, replacement=None, allow_rate_limit_retry=False):
        current = replacement or s0
        calls.append(current.candidate.model_id)
        if current.candidate.model_id == 'm0':
            err = HTTPException(502, detail={'code': 'provider_error', 'message': 'Model request failed.'})
            raise openai_router._CandidateRateLimited(err, current, run_attempt=None, retryable_same_model=False)
        return {'id': 'ok', 'model': current.candidate.model_id}

    sleep_mock = AsyncMock()
    monkeypatch.setattr(openai_router, '_generate_chat_completion', fake_generate)
    monkeypatch.setattr(openai_router, 'select_rate_limit_fallback', AsyncMock(return_value=s1))
    monkeypatch.setattr(openai_router.asyncio, 'sleep', sleep_mock)

    result = await openai_router.generate_chat_completion(
        request=SimpleNamespace(),
        form_data={
            'model': 'agent-runtime-free-medium',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'metadata': {'event_emitter': lambda x: x},
        },
        user=SimpleNamespace(id='u1', role='user'),
    )
    assert result == {'id': 'ok', 'model': 'm1'}
    assert calls == ['m0', 'm1']
    sleep_mock.assert_not_awaited()
