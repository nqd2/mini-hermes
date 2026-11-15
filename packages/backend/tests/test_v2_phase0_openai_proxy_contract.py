"""Selected existing Open WebUI OpenAI proxy contracts without provider traffic."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from open_webui.routers import openai


@pytest.mark.asyncio
async def test_indexed_model_listing_requires_admin_and_disabled_provider_returns_503(monkeypatch):
    async def disabled(_key, default=None):
        return False

    monkeypatch.setattr(openai.Config, 'get', disabled)
    app = FastAPI()
    app.include_router(openai.router, prefix='/openai')
    app.dependency_overrides[openai.get_verified_user] = lambda: SimpleNamespace(id='user-1', role='user')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        denied = await client.get('/openai/models/0')
    assert denied.status_code == 401

    app.dependency_overrides[openai.get_verified_user] = lambda: SimpleNamespace(id='admin-1', role='admin')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        disabled_response = await client.get('/openai/models/0')
    assert disabled_response.status_code == 503
    assert disabled_response.json()['detail'] == 'OpenAI API is disabled'


@pytest.mark.asyncio
async def test_model_management_rejects_unsupported_provider_before_upstream_request(monkeypatch):
    async def config_get(_key, default=None):
        return True

    async def connection(_idx):
        return 'https://provider.example.invalid/v1', 'provider-key', {'provider': 'custom'}

    monkeypatch.setattr(openai.Config, 'get', config_get)
    monkeypatch.setattr(openai, 'get_openai_connection', connection)
    app = FastAPI()
    app.include_router(openai.router, prefix='/openai')
    app.dependency_overrides[openai.get_admin_user] = lambda: SimpleNamespace(id='admin-1', role='admin')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/openai/models/0/catalog')
    assert response.status_code == 400
    assert 'does not support model management' in response.json()['detail']
