"""Characterize the current session endpoint without a live account or database."""

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from open_webui.routers import auths


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(auths.router, prefix='/api/v1/auths')
    user = SimpleNamespace(
        id='user-1', email='person@example.invalid', name='Person', role='user',
        profile_image_url='/user.png', bio=None, gender=None, date_of_birth=None,
        status_emoji=None, status_message=None, status_expires_at=None,
    )
    app.dependency_overrides[auths.get_human_user] = lambda: user
    app.dependency_overrides[auths.get_async_session] = lambda: None
    monkeypatch.setattr(auths.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(auths, 'get_permissions', AsyncMock(return_value={'workspace': {'models': False}}))
    return app


@pytest.mark.asyncio
async def test_session_response_contains_identity_permissions_and_http_only_cookie(app, monkeypatch):
    expires_at = int(time.time()) + 3600
    monkeypatch.setattr(auths, 'decode_token', lambda token: {'exp': expires_at})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auths/', headers={'Authorization': 'Bearer example-token'})

    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in ('token', 'token_type', 'id', 'email', 'name', 'role', 'expires_at')} == {
        'token': 'example-token', 'token_type': 'Bearer', 'id': 'user-1',
        'email': 'person@example.invalid', 'name': 'Person', 'role': 'user', 'expires_at': expires_at,
    }
    assert body['permissions'] == {'workspace': {'models': False}}
    cookie = response.headers['set-cookie'].lower()
    assert 'token=example-token' in cookie
    assert 'httponly' in cookie
    assert 'samesite=' in cookie


@pytest.mark.asyncio
async def test_session_returns_401_detail_for_expired_token(app, monkeypatch):
    monkeypatch.setattr(auths, 'decode_token', lambda token: {'exp': int(time.time()) - 5})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auths/', headers={'Authorization': 'Bearer expired-token'})

    assert response.status_code == 401
    assert isinstance(response.json()['detail'], str)
    assert 'set-cookie' not in response.headers


@pytest.mark.asyncio
async def test_session_route_uses_real_auth_dependency_and_rejects_automation_token(app, monkeypatch):
    from open_webui.utils import auth as auth_utils
    from open_webui.utils import mfa

    app.dependency_overrides.pop(auths.get_human_user)
    app.state.redis = None
    user = SimpleNamespace(
        id='user-1', email='person@example.invalid', name='Person', role='user',
        profile_image_url='/user.png', bio=None, gender=None, date_of_birth=None,
        status_emoji=None, status_message=None, status_expires_at=None,
    )
    monkeypatch.setattr(auth_utils.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(auth_utils.Users, 'update_last_active_by_id', AsyncMock(return_value=True))
    monkeypatch.setattr(mfa, 'get_auth', AsyncMock(return_value=SimpleNamespace(session_stamp='stamp-1', mfa=None)))
    monkeypatch.setattr(mfa, 'get_mfa_config', AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=False)))
    session_token = auth_utils.create_token(
        {'id': 'user-1', 'typ': 'session', 'session_stamp': 'stamp-1'}, timedelta(hours=1)
    )
    automation_token = auth_utils.create_token(
        {'id': 'user-1', 'typ': 'automation', 'session_stamp': 'stamp-1'}, timedelta(hours=1)
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        accepted = await client.get('/api/v1/auths/', headers={'Authorization': f'Bearer {session_token}'})
        denied = await client.get('/api/v1/auths/', headers={'Authorization': f'Bearer {automation_token}'})

    assert accepted.status_code == 200
    assert accepted.json()['id'] == 'user-1'
    assert accepted.json()['token'] == session_token
    assert 'httponly' in accepted.headers['set-cookie'].lower()
    assert denied.status_code == 403
    assert denied.json()['detail'] == 'A human session is required.'


@pytest.mark.asyncio
async def test_session_route_rejects_revoked_session_stamp_before_user_lookup(app, monkeypatch):
    from open_webui.utils import auth as auth_utils
    from open_webui.utils import mfa

    app.dependency_overrides.pop(auths.get_human_user)
    app.state.redis = None
    user_lookup = AsyncMock()
    monkeypatch.setattr(auth_utils.Users, 'get_user_by_id', user_lookup)
    monkeypatch.setattr(mfa, 'get_auth', AsyncMock(return_value=SimpleNamespace(session_stamp='new-stamp', mfa=None)))
    token = auth_utils.create_token(
        {'id': 'user-1', 'typ': 'session', 'session_stamp': 'old-stamp'}, timedelta(hours=1)
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auths/', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 401
    assert response.json()['detail'] == 'Invalid token'
    user_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_signout_without_redis_clears_cookie_but_does_not_revoke_bearer_token(app, monkeypatch):
    """Record the existing per-token revocation gap for the V2 auth contract."""
    from open_webui.utils import auth as auth_utils
    from open_webui.utils import mfa

    app.dependency_overrides.pop(auths.get_human_user)
    app.state.redis = None
    user = SimpleNamespace(
        id='user-1', email='person@example.invalid', name='Person', role='user',
        profile_image_url='/user.png', bio=None, gender=None, date_of_birth=None,
        status_emoji=None, status_message=None, status_expires_at=None,
    )
    monkeypatch.setattr(auth_utils.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(auth_utils.Users, 'update_last_active_by_id', AsyncMock(return_value=True))
    monkeypatch.setattr(mfa, 'get_auth', AsyncMock(return_value=SimpleNamespace(session_stamp='stamp-1', mfa=None)))
    monkeypatch.setattr(mfa, 'get_mfa_config', AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=False)))
    monkeypatch.setattr(auths, 'publish_event', AsyncMock())
    token = auth_utils.create_token(
        {'id': 'user-1', 'typ': 'session', 'session_stamp': 'stamp-1'}, timedelta(hours=1)
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        signed_out = await client.post('/api/v1/auths/signout', headers={'Authorization': f'Bearer {token}'})
        still_valid = await client.get('/api/v1/auths/', headers={'Authorization': f'Bearer {token}'})

    assert signed_out.status_code == 200
    assert signed_out.json() == {'status': True}
    assert 'token=' in signed_out.headers['set-cookie'].lower()
    assert still_valid.status_code == 200
