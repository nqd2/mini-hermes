"""Regression tests for Google OAuth pending signup and frontend redirect resolution."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response

from weai.utils import oauth as oauth_utils


class _FakeGoogleClient:
    def __init__(self, userinfo: dict):
        self._userinfo = userinfo

    async def authorize_access_token(self, request, **_kwargs):
        return {
            'access_token': 'google-access-token',
            'expires_in': 3600,
            'userinfo': self._userinfo,
        }


@pytest.mark.asyncio
async def test_google_oauth_signup_after_bootstrap_creates_pending_user(monkeypatch):
    manager = oauth_utils.OAuthManager(app=SimpleNamespace())
    client = _FakeGoogleClient(
        {
            'sub': 'google-sub-123',
            'email': 'new.member@example.com',
            'email_verified': True,
            'name': 'New Member',
        }
    )
    monkeypatch.setattr(manager, 'get_client', lambda _provider: client)
    monkeypatch.setitem(oauth_utils.OAUTH_PROVIDERS, 'google', {'register': lambda _o: None})

    runtime_cfg = SimpleNamespace(
        ENABLE_OAUTH=True,
        ENABLE_OAUTH_SIGNUP=True,
        DEFAULT_USER_ROLE='pending',
        OAUTH_MERGE_ACCOUNTS_BY_EMAIL=False,
        ENABLE_OAUTH_ROLE_MANAGEMENT=False,
        ENABLE_OAUTH_GROUP_MANAGEMENT=False,
        ENABLE_OAUTH_GROUP_CREATION=False,
        OAUTH_GROUP_DEFAULT_SHARE=True,
        OAUTH_BLOCKED_GROUPS='[]',
        OAUTH_ROLES_CLAIM='roles',
        OAUTH_SUB_CLAIM=None,
        OAUTH_GROUPS_CLAIM='groups',
        OAUTH_EMAIL_CLAIM='email',
        OAUTH_PICTURE_CLAIM='picture',
        OAUTH_USERNAME_CLAIM='name',
        OAUTH_ALLOWED_ROLES=['user', 'admin'],
        OAUTH_ADMIN_ROLES=['admin'],
        OAUTH_ALLOWED_DOMAINS=['*'],
        WEBHOOK_URL='',
        JWT_EXPIRES_IN='7d',
        OAUTH_UPDATE_PICTURE_ON_LOGIN=False,
        OAUTH_UPDATE_NAME_ON_LOGIN=False,
        OAUTH_UPDATE_EMAIL_ON_LOGIN=False,
    )
    monkeypatch.setattr(oauth_utils, 'get_oauth_runtime_config', AsyncMock(return_value=runtime_cfg))
    monkeypatch.setattr(oauth_utils.Users, 'get_user_by_oauth_sub', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth_utils.Users, 'get_user_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth_utils.Users, 'has_users', AsyncMock(return_value=True))
    monkeypatch.setattr(oauth_utils, 'get_password_hash', AsyncMock(return_value='hashed'))
    monkeypatch.setattr(oauth_utils, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(oauth_utils, 'publish_event', AsyncMock())
    monkeypatch.setattr(oauth_utils.Config, 'get', AsyncMock(side_effect=lambda k, *_a: 'http://127.0.0.1:8080' if k == 'weai.url' else None))
    monkeypatch.setattr(oauth_utils, 'CORS_ALLOW_ORIGIN', ['http://127.0.0.1:5173', 'http://localhost:5173'])
    monkeypatch.setattr(oauth_utils, 'FRONTEND_BUILD_DIR', Path('/nonexistent-frontend-build'))
    monkeypatch.setattr(oauth_utils.OAuthSessions, 'get_sessions_by_user_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(
        oauth_utils.OAuthSessions,
        'create_session',
        AsyncMock(return_value=SimpleNamespace(id='sess-1')),
    )

    created_user = SimpleNamespace(
        id='user-2',
        email='new.member@example.com',
        name='New Member',
        role='pending',
        profile_image_url='/user.png',
        oauth={'google': {'sub': 'google-sub-123'}},
    )
    insert_mock = AsyncMock(return_value=created_user)
    monkeypatch.setattr(oauth_utils.Auths, 'insert_new_auth', insert_mock)

    import weai.utils.auth as auth_utils

    monkeypatch.setattr(
        auth_utils,
        'create_signin_response',
        AsyncMock(return_value={'next_step': 'pending'}),
    )

    request = SimpleNamespace(
        base_url='http://127.0.0.1:8080/',
        headers={},
        session={'oauth_frontend_origin': 'http://127.0.0.1:5173'},
    )
    res = await manager.handle_callback(request, 'google', Response(), db=SimpleNamespace())

    insert_mock.assert_awaited_once()
    kwargs = insert_mock.call_args.kwargs
    assert kwargs['role'] == 'pending'
    assert kwargs['bootstrap_only'] is False
    assert res.headers['location'] == 'http://127.0.0.1:5173/auth?pending=1'


@pytest.mark.asyncio
async def test_google_oauth_unverified_email_cannot_auto_signup(monkeypatch):
    manager = oauth_utils.OAuthManager(app=SimpleNamespace())
    client = _FakeGoogleClient(
        {
            'sub': 'google-sub-unverified',
            'email': 'unverified@example.com',
            'email_verified': False,
            'name': 'Unverified',
        }
    )
    monkeypatch.setattr(manager, 'get_client', lambda _provider: client)
    monkeypatch.setitem(oauth_utils.OAUTH_PROVIDERS, 'google', {'register': lambda _o: None})

    runtime_cfg = SimpleNamespace(
        ENABLE_OAUTH=True,
        ENABLE_OAUTH_SIGNUP=True,
        DEFAULT_USER_ROLE='pending',
        OAUTH_MERGE_ACCOUNTS_BY_EMAIL=False,
        ENABLE_OAUTH_ROLE_MANAGEMENT=False,
        ENABLE_OAUTH_GROUP_MANAGEMENT=False,
        ENABLE_OAUTH_GROUP_CREATION=False,
        OAUTH_GROUP_DEFAULT_SHARE=True,
        OAUTH_BLOCKED_GROUPS='[]',
        OAUTH_ROLES_CLAIM='roles',
        OAUTH_SUB_CLAIM=None,
        OAUTH_GROUPS_CLAIM='groups',
        OAUTH_EMAIL_CLAIM='email',
        OAUTH_PICTURE_CLAIM='picture',
        OAUTH_USERNAME_CLAIM='name',
        OAUTH_ALLOWED_ROLES=['user', 'admin'],
        OAUTH_ADMIN_ROLES=['admin'],
        OAUTH_ALLOWED_DOMAINS=['*'],
        WEBHOOK_URL='',
        JWT_EXPIRES_IN='7d',
        OAUTH_UPDATE_PICTURE_ON_LOGIN=False,
        OAUTH_UPDATE_NAME_ON_LOGIN=False,
        OAUTH_UPDATE_EMAIL_ON_LOGIN=False,
    )
    monkeypatch.setattr(oauth_utils, 'get_oauth_runtime_config', AsyncMock(return_value=runtime_cfg))
    monkeypatch.setattr(oauth_utils.Users, 'get_user_by_oauth_sub', AsyncMock(return_value=None))
    monkeypatch.setattr(oauth_utils.Users, 'has_users', AsyncMock(return_value=True))
    insert_mock = AsyncMock()
    monkeypatch.setattr(oauth_utils.Auths, 'insert_new_auth', insert_mock)
    monkeypatch.setattr(oauth_utils.Config, 'get', AsyncMock(return_value='http://127.0.0.1:8080'))
    monkeypatch.setattr(oauth_utils, 'FRONTEND_BUILD_DIR', Path('/nonexistent-frontend-build'))
    monkeypatch.setattr(oauth_utils, 'CORS_ALLOW_ORIGIN', ['http://127.0.0.1:5173'])

    request = SimpleNamespace(base_url='http://127.0.0.1:8080/', headers={}, session={})
    res = await manager.handle_callback(request, 'google', Response(), db=SimpleNamespace())

    insert_mock.assert_not_awaited()
    assert 'error=' in res.headers['location']


@pytest.mark.asyncio
async def test_pending_user_always_returns_pending_next_step_even_when_mfa_disabled(monkeypatch):
    from weai.utils import auth as auth_utils
    from weai.utils import mfa as mfa_utils

    auth_row = SimpleNamespace(session_stamp='stamp-1', password='pw')
    monkeypatch.setattr(mfa_utils, 'get_auth', AsyncMock(return_value=auth_row))
    monkeypatch.setattr(mfa_utils, 'get_mfa_config', AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=False)))

    pending_user = SimpleNamespace(id='user-pending', role='pending')
    result = await auth_utils.create_signin_response(
        request=SimpleNamespace(),
        user=pending_user,
        source='oauth',
    )
    assert result == {'next_step': 'pending'}
