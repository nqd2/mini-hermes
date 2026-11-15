"""Regression for final JSON body limits on the optional OpenAI passthrough."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from open_webui.routers import openai


@pytest.fixture
def proxy_app(monkeypatch):
    app = FastAPI()
    app.include_router(openai.router, prefix='/openai')
    app.dependency_overrides[openai.get_verified_user] = lambda: SimpleNamespace(id='user-1', role='user')
    app.state.OPENAI_MODELS = {}
    monkeypatch.setattr(openai, 'ENABLE_OPENAI_API_PASSTHROUGH', True)
    monkeypatch.setattr(openai, 'get_openai_connection', AsyncMock(return_value=('https://provider.invalid/v1', '', {})))
    monkeypatch.setattr(openai, 'get_headers_and_cookies', AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(openai, 'cleanup_response', AsyncMock())
    response = SimpleNamespace(status=200, headers={}, json=AsyncMock(return_value={'ok': True}))
    session = SimpleNamespace(request=AsyncMock(return_value=response))
    monkeypatch.setattr(openai, 'get_session', AsyncMock(return_value=session))
    return app, session


@pytest.mark.asyncio
@pytest.mark.parametrize('wire', ['["' + ('x' * 99997) + '"]', '"' + ('x' * 99999) + '"'], ids=['array', 'scalar'])
async def test_passthrough_rejects_oversized_non_object_json_before_send(proxy_app, wire):
    app, session = proxy_app
    assert len(wire) == 100001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=wire, headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'request_json_char_limit_exceeded'
    session.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_passthrough_preserves_exact_non_object_json_at_limit(proxy_app):
    app, session = proxy_app
    wire = '"' + ('x' * 99998) + '"'
    assert len(wire) == 100000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=wire, headers={'content-type': 'application/json'})
    assert response.status_code == 200
    assert session.request.await_args.kwargs['data'] == wire.encode('utf-8')


@pytest.mark.asyncio
async def test_passthrough_rejects_non_object_json_over_byte_budget(proxy_app):
    app, session = proxy_app
    wire = '"' + ('😀' * 96000) + '"'
    assert len(wire) < 100000 and len(wire.encode('utf-8')) > 380000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=wire.encode('utf-8'),
                                     headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'request_json_byte_limit_exceeded'
    session.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [b'null', b'\x00\xff'], ids=['json-null', 'non-json-binary'])
async def test_passthrough_preserves_small_null_and_non_json_bodies(proxy_app, body):
    app, session = proxy_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=body)
    assert response.status_code == 200
    assert session.request.await_args.kwargs['data'] == body


@pytest.mark.asyncio
@pytest.mark.parametrize('content_type', ['application/json; charset=utf-8', 'application/problem+json'],
                         ids=['json-charset', 'json-suffix'])
async def test_oversized_malformed_declared_json_is_rejected_before_send(proxy_app, content_type):
    app, session = proxy_app
    wire = b'{' + b'x' * 100000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=wire, headers={'content-type': content_type})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'request_json_char_limit_exceeded'
    session.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_declared_json_invalid_utf8_is_rejected_before_send(proxy_app):
    app, session = proxy_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=b'\xff',
                                     headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'request_json_invalid_utf8'
    session.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_json_outbound_header_cannot_bypass_limit_with_non_json_input(proxy_app, monkeypatch):
    app, session = proxy_app
    monkeypatch.setattr(openai, 'get_headers_and_cookies',
                        AsyncMock(return_value=({'Content-Type': 'application/json'}, {})))
    wire = b'x' * 100001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/openai/experimental', content=wire, headers={'content-type': 'text/plain'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'request_json_char_limit_exceeded'
    session.request.assert_not_awaited()
