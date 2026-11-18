"""The Office Agent surface excludes retired WeAI products."""

from fastapi.routing import APIRoute
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from weai.main import app
from weai import main
from weai.config import DEFAULT_CONFIG
from weai.models.chats import Chats
from weai.utils.inbox import source_models


def test_retired_product_apis_are_not_mounted():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    removed_prefixes = (
        '/api/v1/apps',
        '/api/v1/automations',
        '/api/v1/calendars',
        '/api/v1/channels',
        '/api/v1/comments',
        '/api/v1/evaluations',
        '/api/v1/functions',
        '/api/v1/notes',
        '/api/v1/pipelines',
        '/api/v1/terminals',
        '/api/v1/audio',
    )
    for prefix in removed_prefixes:
        assert not any(path == prefix or path.startswith(prefix + '/') for path in paths), prefix


def test_office_agent_apis_remain_mounted():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    required_prefixes = (
        '/api/v1/auths',
        '/api/v1/chats',
        '/api/v1/models',
        '/api/v1/agent-runtime',
        '/api/v1/skills',
        '/api/v1/tools',
    )
    assert not any(path.startswith('/api/v1/mini-hermes/') for path in paths)
    for prefix in required_prefixes:
        assert any(path.startswith(prefix + '/') for path in paths), prefix


def test_public_chat_sharing_routes_are_not_mounted():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    retired = {
        '/api/v1/chats/share/all',
        '/api/v1/chats/shared',
        '/api/v1/chats/share/{share_id}',
        '/api/v1/chats/{id}/clone/shared',
        '/api/v1/chats/{id}/share',
        '/api/v1/chats/shared/{id}/access/update',
        '/api/v1/chats/shared/{id}/access',
    }
    assert paths.isdisjoint(retired)


@pytest.mark.asyncio
async def test_chat_access_does_not_depend_on_retired_public_share_snapshot():
    chat = SimpleNamespace(user_id='owner', folder_id=None, meta={})
    requester = SimpleNamespace(id='other-user', role='user')

    assert await Chats.get_accessible_chat_by_id('chat-1', requester, chat=chat) is None


def test_directory_sign_in_routes_are_not_mounted():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert paths.isdisjoint({
        '/api/v1/auths/ldap',
        '/api/v1/auths/admin/config/ldap',
        '/api/v1/auths/admin/config/ldap/server',
    })


def test_onedrive_configuration_is_absent_from_product_defaults():
    assert not any(key.startswith('onedrive.') for key in DEFAULT_CONFIG)


def test_ollama_routes_and_configuration_are_absent():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert not any(path.startswith('/ollama/') or path.startswith('/api/ollama/') for path in paths)
    assert not any(key.startswith(('ollama.', 'rag.ollama.')) for key in DEFAULT_CONFIG)


def test_default_permissions_exclude_retired_sharing_products():
    assert {'notes', 'public_notes', 'public_chats', 'open_chats', 'public_calendars'}.isdisjoint(
        DEFAULT_CONFIG['user.permissions']['sharing']
    )


def test_direct_image_generation_routes_are_not_mounted():
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert {
        '/api/v1/images/generations',
        '/api/v1/images/edit',
        '/api/v1/tasks/image_prompt/completions',
    }.isdisjoint(paths)


def test_notification_sources_exclude_retired_products():
    assert source_models() == {}


@pytest.mark.asyncio
async def test_client_config_does_not_advertise_retired_products(monkeypatch):
    monkeypatch.setattr(main, 'decode_token', lambda _: {'id': 'user-1'})
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(main.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='user-1', role='user')))
    monkeypatch.setattr(main.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(main.Config, 'get_many', AsyncMock(return_value={
        'channels.enable': True, 'channels.encryption.enable': True,
        'apps.enable': True, 'calendar.enable': True, 'automations.enable': True,
        'notes.enable': True, 'comments.enable': True, 'subagents.enable': True,
        'onedrive.enable': True, 'ui.enable_community_sharing': True,
        'ui.enable_user_webhooks': True,
    }))
    monkeypatch.setattr(main.app.state, 'redis', None, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/config', headers={'Authorization': 'Bearer test-token'})
    assert response.status_code == 200
    body = response.json()
    retired = {
        'enable_apps', 'enable_automations', 'enable_calendar', 'enable_channels',
        'enable_encrypted_channels', 'enable_comments', 'enable_notes',
        'enable_subagents', 'enable_onedrive_integration', 'enable_community_sharing',
        'enable_user_webhooks', 'enable_functions', 'enable_terminal_servers',
    }
    assert retired.isdisjoint(body['features'])
    assert 'onedrive' not in body
    assert 'audio' not in body


@pytest.mark.asyncio
async def test_client_config_does_not_expose_weai_license_data(monkeypatch):
    monkeypatch.setattr(main, 'decode_token', lambda _: {'id': 'user-1'})
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=True))
    monkeypatch.setattr(main.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='user-1', role='admin')))
    monkeypatch.setattr(main.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(main.Config, 'get_many', AsyncMock(return_value={}))
    monkeypatch.setattr(main.Users, 'get_num_users', AsyncMock(return_value=100))
    monkeypatch.setattr(main.app.state, 'redis', None, raising=False)
    monkeypatch.setattr(main.app.state, 'LICENSE_METADATA', {'type': 'trial', 'seats': 10}, raising=False)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/config', headers={'Authorization': 'Bearer test-token'})

    assert response.status_code == 200
    body = response.json()
    assert 'license_metadata' not in body
    assert 'user_count' not in body
    assert 'active_entries' not in body
