from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from weai.events import EVENTS
from weai.model_routing.tier_service import (
    DEFAULT_PLUS_MONTHLY_LIMIT,
    PlanSnapshot,
    TierRepository,
    validate_plan_inputs,
)
from weai.models.auths import AddUserForm
from weai.models.users import UserModel
from weai.routers import auths as auths_router
from weai.routers import users as users_router


def _make_user(user_id: str, email: str, name: str, role: str = 'user') -> UserModel:
    return UserModel(
        id=user_id,
        email=email,
        name=name,
        role=role,
        profile_image_url='/user.png',
        last_active_at=1700000000,
        updated_at=1700000000,
        created_at=1700000000,
    )


def test_add_user_form_validation():
    default_form = AddUserForm(name='Alice', email='alice@example.com', password='Password123!')
    assert default_form.tier == 'free'
    assert default_form.monthly_request_limit is None

    plus_explicit = AddUserForm(
        name='Bob',
        email='bob@example.com',
        password='Password123!',
        tier='plus',
        monthly_request_limit=250,
    )
    assert plus_explicit.tier == 'plus'
    assert plus_explicit.monthly_request_limit == 250

    plus_default = AddUserForm(
        name='Carol',
        email='carol@example.com',
        password='Password123!',
        tier='plus',
    )
    assert plus_default.tier == 'plus'
    assert plus_default.monthly_request_limit is None

    for bad_kwargs in (
        {'tier': 'enterprise'},
        {'tier': 'plus', 'monthly_request_limit': 0},
        {'tier': 'plus', 'monthly_request_limit': -10},
        {'tier': 'plus', 'monthly_request_limit': True},
        {'tier': 'plus', 'monthly_request_limit': '100'},
        {'tier': 'plus', 'monthly_request_limit': 2**31},
    ):
        with pytest.raises(ValidationError):
            AddUserForm(name='Bad', email='bad@example.com', password='Password123!', **bad_kwargs)


def test_validate_plan_inputs():
    validate_plan_inputs('free', None)
    validate_plan_inputs('plus', 1)
    validate_plan_inputs('plus', DEFAULT_PLUS_MONTHLY_LIMIT)

    for tier, limit in (
        ('free', 100),
        ('plus', None),
        ('plus', 0),
        ('plus', True),
        ('plus', 2**31),
        ('unknown', None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            validate_plan_inputs(tier, limit)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == {'code': 'invalid_plan'}


def _build_auths_app(admin_user: UserModel, fake_db):
    app = FastAPI()
    app.include_router(auths_router.router, prefix='/auths')
    app.dependency_overrides[auths_router.get_admin_user] = lambda: admin_user

    async def _db_override():
        yield fake_db

    app.dependency_overrides[auths_router.get_async_session] = _db_override
    return app


@pytest.mark.asyncio
async def test_add_user_rejects_free_with_limit_before_insert(monkeypatch):
    admin = _make_user('admin-1', 'admin@example.com', 'Admin', role='admin')
    fake_db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    app = _build_auths_app(admin, fake_db)

    get_by_email = AsyncMock(return_value=None)
    insert_new_auth = AsyncMock()
    set_plan_in_db = AsyncMock()
    monkeypatch.setattr(auths_router.Users, 'get_user_by_email', get_by_email)
    monkeypatch.setattr(auths_router.Auths, 'insert_new_auth', insert_new_auth)
    monkeypatch.setattr(auths_router.tier_repository, 'set_plan_in_db', set_plan_in_db)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/auths/add',
            json={
                'name': 'Free With Limit',
                'email': 'free@example.com',
                'password': 'Password123!',
                'role': 'user',
                'tier': 'free',
                'monthly_request_limit': 100,
            },
        )

    assert response.status_code == 422
    assert response.json()['detail'] == {'code': 'invalid_plan'}
    get_by_email.assert_not_awaited()
    insert_new_auth.assert_not_awaited()
    set_plan_in_db.assert_not_awaited()
    fake_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload_extra', 'expected_tier', 'expected_limit'),
    [
        ({}, 'free', None),
        ({'tier': 'plus'}, 'plus', DEFAULT_PLUS_MONTHLY_LIMIT),
        ({'tier': 'plus', 'monthly_request_limit': 250}, 'plus', 250),
    ],
)
async def test_add_user_persists_plan_and_emits_tier_in_user_created_event(
    monkeypatch, payload_extra, expected_tier, expected_limit
):
    admin = _make_user('admin-1', 'admin@example.com', 'Admin', role='admin')
    created = _make_user('user-new', 'new@example.com', 'New User', role='user')
    fake_db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    app = _build_auths_app(admin, fake_db)

    insert_new_auth = AsyncMock(return_value=created)
    set_plan_in_db = AsyncMock(return_value=PlanSnapshot(expected_tier, expected_limit))
    publish_event = AsyncMock()

    monkeypatch.setattr(auths_router.Users, 'get_user_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(auths_router, 'validate_password', lambda _pw: None)
    monkeypatch.setattr(auths_router, 'get_password_hash', AsyncMock(return_value='hashed-pw'))
    monkeypatch.setattr(auths_router.Auths, 'insert_new_auth', insert_new_auth)
    monkeypatch.setattr(auths_router.Auths, 'get_auth_by_id', AsyncMock(return_value=SimpleNamespace(session_stamp='s1')))
    monkeypatch.setattr(auths_router.tier_repository, 'set_plan_in_db', set_plan_in_db)
    monkeypatch.setattr(auths_router, 'apply_default_group_assignment', AsyncMock())
    monkeypatch.setattr(auths_router, 'publish_event', publish_event)
    monkeypatch.setattr(auths_router.Config, 'get', AsyncMock(side_effect=lambda key: '1h' if key == 'auth.jwt_expiry' else None))
    monkeypatch.setattr(auths_router, 'get_mfa_config', AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=False)))
    monkeypatch.setattr(auths_router, 'create_token', lambda **_kwargs: 'jwt-token')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/auths/add',
            json={
                'name': 'New User',
                'email': 'new@example.com',
                'password': 'Password123!',
                'role': 'user',
                **payload_extra,
            },
        )

    assert response.status_code == 200
    insert_new_auth.assert_awaited_once_with(
        'new@example.com',
        'hashed-pw',
        'New User',
        '/user.png',
        'user',
        db=fake_db,
        commit=False,
    )
    set_plan_in_db.assert_awaited_once_with(
        fake_db,
        'user-new',
        expected_tier,
        expected_limit,
        'admin-1',
    )
    fake_db.commit.assert_awaited_once()
    fake_db.rollback.assert_not_awaited()

    publish_event.assert_awaited_once()
    _, event_name = publish_event.await_args.args
    event_kwargs = publish_event.await_args.kwargs
    assert event_name == EVENTS.USER_CREATED
    assert event_kwargs['subject_id'] == 'user-new'
    assert event_kwargs['data'] == {
        'role': 'user',
        'tier': expected_tier,
        'monthly_request_limit': expected_limit,
    }


@pytest.mark.asyncio
async def test_add_user_rolls_back_when_plan_persistence_fails(monkeypatch):
    admin = _make_user('admin-1', 'admin@example.com', 'Admin', role='admin')
    created = _make_user('user-orphan', 'orphan@example.com', 'Orphan', role='user')
    fake_db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    app = _build_auths_app(admin, fake_db)

    monkeypatch.setattr(auths_router.Users, 'get_user_by_email', AsyncMock(return_value=None))
    monkeypatch.setattr(auths_router, 'validate_password', lambda _pw: None)
    monkeypatch.setattr(auths_router, 'get_password_hash', AsyncMock(return_value='hashed-pw'))
    monkeypatch.setattr(auths_router.Auths, 'insert_new_auth', AsyncMock(return_value=created))
    monkeypatch.setattr(
        auths_router.tier_repository,
        'set_plan_in_db',
        AsyncMock(side_effect=RuntimeError('plan write failed')),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/auths/add',
            json={
                'name': 'Orphan',
                'email': 'orphan@example.com',
                'password': 'Password123!',
                'role': 'user',
                'tier': 'plus',
            },
        )

    assert response.status_code == 500
    fake_db.rollback.assert_awaited_once()
    fake_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_plans_by_user_ids_batch_and_defaults():
    repo = TierRepository()
    fake_result = SimpleNamespace(
        all=lambda: [SimpleNamespace(user_id='u1', tier='plus', monthly_request_limit=500)]
    )
    fake_db = SimpleNamespace(execute=AsyncMock(return_value=fake_result))

    assert await repo.get_plans_by_user_ids([], db=fake_db) == {}
    fake_db.execute.assert_not_awaited()

    plans_map = await repo.get_plans_by_user_ids(['u1', 'u2', 'u1'], db=fake_db)
    assert fake_db.execute.await_count == 1
    assert plans_map == {
        'u1': PlanSnapshot('plus', 500),
        'u2': PlanSnapshot('free', None),
    }


@pytest.mark.asyncio
async def test_get_users_batch_loads_plans_and_returns_tier_fields(monkeypatch):
    admin = _make_user('admin-1', 'admin@example.com', 'Admin', role='admin')
    u1 = _make_user('u1', 'u1@example.com', 'User One', role='user')
    u2 = _make_user('u2', 'u2@example.com', 'User Two', role='pending')
    fake_db = SimpleNamespace()

    app = FastAPI()
    app.include_router(users_router.router, prefix='/users')
    app.dependency_overrides[users_router.get_admin_user] = lambda: admin

    async def _db_override():
        yield fake_db

    app.dependency_overrides[users_router.get_async_session] = _db_override

    get_plans = AsyncMock(
        return_value={
            'u1': PlanSnapshot('plus', 750),
            'u2': PlanSnapshot('free', None),
        }
    )
    monkeypatch.setattr(users_router.Users, 'get_users', AsyncMock(return_value={'users': [u1, u2], 'total': 2}))
    monkeypatch.setattr(
        users_router.Groups,
        'get_groups_by_member_ids',
        AsyncMock(return_value={'u1': [SimpleNamespace(id='g1')], 'u2': []}),
    )
    monkeypatch.setattr(users_router.tier_repository, 'get_plans_by_user_ids', get_plans)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/users/')

    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 2
    by_id = {item['id']: item for item in body['users']}
    assert by_id['u1']['tier'] == 'plus'
    assert by_id['u1']['monthly_request_limit'] == 750
    assert by_id['u1']['group_ids'] == ['g1']
    assert by_id['u2']['tier'] == 'free'
    assert by_id['u2']['monthly_request_limit'] is None
    assert by_id['u2']['group_ids'] == []
    get_plans.assert_awaited_once_with(['u1', 'u2'], db=fake_db)
