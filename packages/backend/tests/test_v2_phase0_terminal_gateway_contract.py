"""Selected WeAI terminal-gateway wire contracts, with the upstream mocked."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from weai.routers import terminals


@pytest.mark.asyncio
async def test_terminal_proxy_keeps_identity_session_and_upstream_response(monkeypatch):
    connection = {
        'id': 'one', 'url': 'https://terminal.example.invalid', 'name': 'Test terminal',
        'key': 'upstream-secret', 'auth_type': 'bearer', 'enabled': True,
    }
    monkeypatch.setattr(terminals, 'ENABLE_TOOL_SERVERS', True)

    async def config_get(key, default=None):
        return [connection] if key == 'terminal_server.connections' else default

    async def groups(*args, **kwargs):
        return []

    async def allow(*args, **kwargs):
        return True

    monkeypatch.setattr(terminals.Config, 'get', config_get)
    monkeypatch.setattr(terminals.Groups, 'get_groups_by_member_id', groups)
    monkeypatch.setattr(terminals, 'has_connection_access', allow)
    monkeypatch.setattr(terminals, 'get_terminal_server_url', lambda _connection: connection['url'])
    monkeypatch.setattr(terminals, 'terminal_context_available', lambda _connection, _scope: True)
    monkeypatch.setattr(terminals, 'terminal_context_config', lambda _connection, _scope: {})
    monkeypatch.setattr(terminals, 'terminal_context_id', lambda _connection, _context, _scope: None)

    calls = []

    class UpstreamResponse:
        status = 200
        headers = {'content-type': 'application/json'}

        async def read(self):
            return b'{"entries":[]}'

        async def release(self):
            pass

    class UpstreamSession:
        def __init__(self, **kwargs):
            pass

        async def request(self, **kwargs):
            calls.append(kwargs)
            return UpstreamResponse()

        async def close(self):
            pass

    monkeypatch.setattr(terminals.aiohttp, 'ClientSession', UpstreamSession)
    app = FastAPI()
    app.include_router(terminals.router, prefix='/api/v1/terminals')
    app.dependency_overrides[terminals.get_verified_user] = lambda: SimpleNamespace(id='user-1', role='user')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        listing = await client.get('/api/v1/terminals/')
        proxied = await client.get('/api/v1/terminals/one/files/list?directory=%2F', headers={
            'X-Session-Id': 'chat-1', 'Authorization': 'Bearer client-token',
        })
    assert listing.status_code == 200
    assert [item['id'] for item in listing.json()] == ['one']
    assert proxied.status_code == 200 and json.loads(proxied.content) == {'entries': []}
    assert len(calls) == 1
    assert calls[0]['method'] == 'GET'
    assert calls[0]['url'].startswith('https://terminal.example.invalid/files/list?')
    assert calls[0]['headers']['X-User-Id'] == 'user-1'
    assert calls[0]['headers']['X-Session-Id'] == 'chat-1'
    assert calls[0]['headers']['Authorization'] == 'Bearer upstream-secret'


@pytest.mark.asyncio
async def test_terminal_proxy_blocks_admin_path_before_upstream_call(monkeypatch):
    connection = {'id': 'one', 'url': 'https://terminal.example.invalid', 'enabled': True}
    monkeypatch.setattr(terminals, 'ENABLE_TOOL_SERVERS', True)

    async def config_get(key, default=None):
        return [connection] if key == 'terminal_server.connections' else default

    async def groups(*args, **kwargs):
        return []

    async def allow(*args, **kwargs):
        return True

    monkeypatch.setattr(terminals.Config, 'get', config_get)
    monkeypatch.setattr(terminals.Groups, 'get_groups_by_member_id', groups)
    monkeypatch.setattr(terminals, 'has_connection_access', allow)
    monkeypatch.setattr(terminals, 'get_terminal_server_url', lambda _connection: connection['url'])
    app = FastAPI()
    app.include_router(terminals.router, prefix='/api/v1/terminals')
    app.dependency_overrides[terminals.get_verified_user] = lambda: SimpleNamespace(id='user-1', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/terminals/one/api/v1/policies')
    assert response.status_code == 403
    assert response.json() == {'error': 'Path not allowed'}
