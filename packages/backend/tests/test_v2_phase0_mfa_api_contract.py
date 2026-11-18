"""Characterize MFA HTTP safeguards with service and identity dependencies mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from weai.routers import mfa as routes


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.state.redis = None
    app.include_router(routes.router, prefix='/api/v1/auths/mfa')
    monkeypatch.setattr(routes.mfa, 'get_mfa_config', AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=True)))
    monkeypatch.setattr(routes.ip_limiter, 'is_limited', AsyncMock(return_value=False))
    monkeypatch.setattr(routes.Config, 'get', AsyncMock(return_value='https://example.invalid'))
    return app


@pytest.mark.asyncio
async def test_challenge_rejects_wrong_origin_before_loading_cookie(app, monkeypatch):
    load = AsyncMock()
    monkeypatch.setattr(routes.mfa, 'load_challenge', load)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.invalid') as client:
        response = await client.post('/api/v1/auths/mfa/challenge', headers={'Origin': 'https://attacker.invalid'})
    assert response.status_code == 403
    assert response.json() == {'detail': 'Invalid request origin.'}
    assert response.headers['cache-control'] == 'no-store'
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_challenge_clears_scoped_cookie_and_does_not_expose_error(app, monkeypatch):
    load = AsyncMock(side_effect=HTTPException(401, 'internal detail'))
    monkeypatch.setattr(routes.mfa, 'load_challenge', load)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.invalid') as client:
        response = await client.post('/api/v1/auths/mfa/challenge', headers={'Origin': 'https://example.invalid'},
                                     cookies={'mfa_challenge': 'synthetic-challenge'})
    assert response.status_code == 401
    assert response.json() == {'detail': 'This authentication step expired. Please start again.'}
    assert response.headers['cache-control'] == 'no-store'
    cookie = response.headers['set-cookie'].lower()
    assert 'mfa_challenge=' in cookie and 'path=/api/v1/auths/mfa' in cookie and 'max-age=0' in cookie
    load.assert_awaited_once_with('synthetic-challenge', {'enroll', 'verify', 'recover'})


@pytest.mark.asyncio
async def test_mfa_rate_limit_returns_retry_after_before_service_call(app, monkeypatch):
    monkeypatch.setattr(routes.ip_limiter, 'is_limited', AsyncMock(return_value=True))
    verify = AsyncMock()
    monkeypatch.setattr(routes.mfa, 'verify_mfa_challenge', verify)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/auths/mfa/verify', json={'challenge_token': 'x' * 24, 'code': '123456'})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '900'
    assert response.headers['cache-control'] == 'no-store'
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_mfa_validation_and_database_errors_are_sanitized(app, monkeypatch):
    verify = AsyncMock(side_effect=SQLAlchemyError('internal database details'))
    monkeypatch.setattr(routes.mfa, 'verify_mfa_challenge', verify)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        invalid = await client.post('/api/v1/auths/mfa/verify', json={'challenge_token': 'short', 'code': '123456'})
        unavailable = await client.post('/api/v1/auths/mfa/verify', json={'challenge_token': 'x' * 24, 'code': '123456'})
    assert invalid.status_code == 422 and invalid.json() == {'detail': 'Invalid authentication request.'}
    assert unavailable.status_code == 503 and unavailable.json() == {'detail': 'Authentication is temporarily unavailable.'}
    assert invalid.headers['cache-control'] == unavailable.headers['cache-control'] == 'no-store'
    verify.assert_awaited_once()
