"""Characterize selected model API wire behavior with mocked identity and persistence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from open_webui.models.models import ModelModel
from open_webui.routers import models


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(models.router, prefix='/api/v1/models')
    app.dependency_overrides[models.get_verified_user] = lambda: SimpleNamespace(id='viewer', role='user')
    app.dependency_overrides[models.get_async_session] = lambda: None
    monkeypatch.setattr(models.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    return app


def sample_model(model_id, owner):
    return ModelModel.model_validate({
        'id': model_id, 'user_id': owner, 'name': model_id, 'base_model_id': 'base',
        'params': {'system': 'private instruction', 'temperature': 0.5},
        'meta': {'profile_image_url': '/static/profile.png'},
        'is_active': True, 'updated_at': 1, 'created_at': 1,
    })


@pytest.mark.asyncio
async def test_model_list_masks_read_only_params_and_preserves_writer_params(app, monkeypatch):
    search = AsyncMock(return_value=SimpleNamespace(items=[sample_model('read-only', 'owner'),
                                                          sample_model('writable', 'owner')], total=2))
    monkeypatch.setattr(models.Models, 'search_models', search)
    monkeypatch.setattr(models.AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value={'writable'}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/models/list', params={
            'query': 'audit', 'view_option': 'shared', 'tag': 'reports',
            'order_by': 'name', 'direction': 'asc', 'page': 2,
        })
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 2
    assert body['items'][0]['write_access'] is False and body['items'][0]['params'] == {}
    assert body['items'][1]['write_access'] is True
    assert body['items'][1]['params']['system'] == 'private instruction'
    assert all('profile_image_url' not in item['meta'] for item in body['items'])
    assert search.await_args.args[0] == 'viewer'
    assert search.await_args.kwargs['skip'] == 30 and search.await_args.kwargs['limit'] == 30
    assert search.await_args.kwargs['filter'] == {
        'query': 'audit', 'view_option': 'shared', 'tag': 'reports',
        'order_by': 'name', 'direction': 'asc', 'user_id': 'viewer',
    }


@pytest.mark.asyncio
async def test_model_export_rejects_selection_if_one_requested_model_is_not_returned(app, monkeypatch):
    monkeypatch.setattr(models.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'has_permission', AsyncMock(return_value=True))
    lookup = AsyncMock(return_value=[sample_model('allowed', 'owner')])
    monkeypatch.setattr(models.Models, 'get_models', lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/models/export', params=[('ids', 'allowed'), ('ids', 'missing')])
    assert response.status_code == 403
    assert 'detail' in response.json()
    assert lookup.await_args.kwargs['ids'] == ['allowed', 'missing']
    assert lookup.await_args.kwargs['writable_by_user_id'] == 'viewer'


@pytest.mark.asyncio
async def test_model_export_rejects_lack_of_workspace_permission_before_lookup(app, monkeypatch):
    monkeypatch.setattr(models.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(models, 'has_permission', AsyncMock(return_value=False))
    lookup = AsyncMock()
    monkeypatch.setattr(models.Models, 'get_models', lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/models/export', params={'ids': 'allowed'})
    assert response.status_code == 401
    assert 'detail' in response.json()
    lookup.assert_not_awaited()
