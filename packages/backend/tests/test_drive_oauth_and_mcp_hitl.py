"""Regression tests for drive.file scope, Picker APIs, MCP 401 guidance, and HITL gate."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.documents import oauth as drive_oauth
from weai.documents import routes as doc_routes
from weai.documents.models import Base as DocumentsBase, DriveCredential
from weai.utils.chat_runtime.tool_execution import (
    execute_tool_call,
    requires_mcp_tool_confirmation,
)
from weai.utils.chat_runtime.tool_resolution import (
    ToolResolutionAdapters,
    _connect_mcp_tool_server,
)


def test_drive_oauth_defaults_to_drive_file_scope(monkeypatch):
    monkeypatch.delenv('AGENT_RUNTIME_GOOGLE_SCOPES', raising=False)
    monkeypatch.setenv('AGENT_RUNTIME_GOOGLE_CLIENT_ID', '990347055283-test.apps.googleusercontent.com')
    monkeypatch.setenv('AGENT_RUNTIME_GOOGLE_REDIRECT_URI', 'http://127.0.0.1:8080/api/v1/agent-runtime/drive/callback')

    assert drive_oauth.drive_scopes() == 'https://www.googleapis.com/auth/drive.file'
    url = drive_oauth.authorization_url('state-123')
    query = parse_qs(urlparse(url).query)
    assert query['scope'] == ['https://www.googleapis.com/auth/drive.file']
    assert query['access_type'] == ['offline']
    assert query['prompt'] == ['consent']


@pytest.mark.asyncio
async def test_has_drive_credential_queries_real_drive_credential_table(monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME_TOKEN_ENCRYPTION_KEY', 'test-encryption-secret')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(DocumentsBase.metadata.create_all, tables=[DriveCredential.__table__])

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        assert await drive_oauth.has_drive_credential(db, 'user-1') is False
        await drive_oauth.save_refresh_token(db, 'user-1', 'refresh-token-123')
        assert await drive_oauth.has_drive_credential(db, 'user-1') is True
        assert await drive_oauth.has_drive_credential(db, 'user-2') is False

    await engine.dispose()


def test_drive_callback_returns_to_workspace_documents_when_referer_present(monkeypatch):
    from pathlib import Path
    from weai import config as weai_config
    from weai import env as weai_env

    monkeypatch.setattr(weai_config, 'CORS_ALLOW_ORIGIN', ['http://127.0.0.1:5173'])
    monkeypatch.setattr(weai_config, 'WEAI_URL', '')
    monkeypatch.setattr(weai_env, 'FRONTEND_BUILD_DIR', Path('/nonexistent-frontend-build'))

    request = SimpleNamespace(
        headers={
            'origin': 'http://127.0.0.1:5173',
            'referer': 'http://127.0.0.1:5173/workspace/documents',
        }
    )
    return_path = doc_routes._resolve_frontend_return_path(request)
    assert return_path == '/workspace/documents'
    assert (
        doc_routes._drive_callback_redirect_url(
            {'redirect_origin': 'http://127.0.0.1:5173', 'redirect_path': return_path}
        )
        == 'http://127.0.0.1:5173/workspace/documents?drive=connected'
    )


def test_file_id_accepts_drive_urls_and_picker_file_ids():
    assert doc_routes._file_id('https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view') == '1AbCdEfGhIjKlMnOpQrStUv'
    assert doc_routes._file_id('1AbCdEfGhIjKlMnOpQrStUv') == '1AbCdEfGhIjKlMnOpQrStUv'
    with pytest.raises(HTTPException) as exc:
        doc_routes._file_id('https://example.invalid/not-drive')
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_drive_status_and_picker_token_do_not_expose_refresh_token(monkeypatch):
    user = SimpleNamespace(id='user-1', role='user')
    monkeypatch.setattr(doc_routes, 'has_drive_credential', AsyncMock(return_value=True))
    monkeypatch.setattr(doc_routes, 'access_token_for', AsyncMock(return_value='short-lived-access-token'))

    status_payload = await doc_routes.drive_status(user=user, db=SimpleNamespace())
    assert status_payload == {'connected': True, 'scopes': ['https://www.googleapis.com/auth/drive.file']}

    picker_payload = await doc_routes.drive_picker_token(user=user, db=SimpleNamespace())
    assert picker_payload == {'connected': True, 'access_token': 'short-lived-access-token'}
    assert 'refresh_token' not in picker_payload


@pytest.mark.asyncio
async def test_import_drive_surfaces_connector_drive_error_as_http_502(monkeypatch):
    from weai.documents.drive import DriveError

    user = SimpleNamespace(id='user-1', role='user')
    monkeypatch.setattr(doc_routes, 'access_token_for', AsyncMock(return_value='short-lived-access-token'))

    class FailingGateway:
        async def call(self, route, **payload):
            raise DriveError('Connector 403: {"detail":"Connector access denied"}')

    monkeypatch.setattr(doc_routes, 'Gateway', FailingGateway)

    with pytest.raises(HTTPException) as exc:
        await doc_routes.import_drive(
            request=doc_routes.LinkRequest(url='https://drive.google.com/file/d/1AjuwwYuIvjA6MGPehXGSw1TUR-fln_F6/view'),
            user=user,
            db=SimpleNamespace(),
        )
    assert exc.value.status_code == 502
    assert 'Connector 403' in str(exc.value.detail)


@pytest.mark.asyncio
async def test_mcp_401_emits_oauth_authorization_guidance():
    events = []

    async def emitter(event):
        events.append(event)

    adapters = SimpleNamespace(
        connect_mcp_server=AsyncMock(side_effect=HTTPException(status_code=401, detail='Auth required')),
        logger=logging.getLogger('test-mcp-401'),
    )
    await _connect_mcp_tool_server(
        request=SimpleNamespace(),
        form_data={'messages': []},
        user=SimpleNamespace(id='user-1'),
        extra_params={'__event_emitter__': emitter},
        tool_id='server:mcp:google-workspace',
        clients={},
        tools={},
        adapters=adapters,
    )

    assert len(events) == 1
    assert events[0]['type'] == 'status'
    assert events[0]['data']['action'] == 'tool_oauth_required'
    assert events[0]['data']['authorization_url'] == '/oauth/clients/mcp:google-workspace/authorize'


@pytest.mark.asyncio
async def test_mcp_hitl_confirmation_gate_blocks_mutating_tools_and_allows_reads():
    calls = []

    async def mcp_read(**kwargs):
        calls.append(('read', kwargs))
        return [{'type': 'text', 'text': 'ok'}]

    async def mcp_delete(**kwargs):
        calls.append(('delete', kwargs))
        return [{'type': 'text', 'text': 'deleted'}]

    read_tool = {
        'type': 'mcp',
        'mcp_tool_name': 'search_files',
        'annotations': {'readOnlyHint': True},
        'callable': mcp_read,
        'direct': False,
        'spec': {'parameters': {'properties': {}}},
    }
    delete_tool = {
        'type': 'mcp',
        'mcp_tool_name': 'delete_file',
        'annotations': {'destructiveHint': True, 'readOnlyHint': False},
        'callable': mcp_delete,
        'direct': False,
        'spec': {'parameters': {'properties': {}}},
    }
    share_tool = {
        'type': 'mcp',
        'mcp_tool_name': 'share_file',
        'annotations': {},
        'callable': mcp_delete,
        'direct': False,
        'spec': {'parameters': {'properties': {}}},
    }

    assert requires_mcp_tool_confirmation('gw_search_files', read_tool) is False
    assert requires_mcp_tool_confirmation('gw_delete_file', delete_tool) is True
    assert requires_mcp_tool_confirmation('gw_share_file', share_tool) is True

    metadata = {
        'chat_id': 'temporary-chat',
        'params': {'tool_approval_mode': 'full'},
        'tools': {
            'gw_search_files': read_tool,
            'gw_delete_file': delete_tool,
        },
    }

    # Read-only MCP tool executes even without event_caller when tool_approval_mode='full'
    _, read_result, *_ = await execute_tool_call(
        {},
        metadata,
        None,
        {'name': 'gw_search_files', 'arguments': '{"query": "budget"}'},
    )
    assert read_result == [{'type': 'text', 'text': 'ok'}]

    # Mutating MCP tool fails closed without event_caller
    _, blocked_result, *_ = await execute_tool_call(
        {},
        metadata,
        None,
        {'name': 'gw_delete_file', 'arguments': '{"file_id": "file-1"}'},
    )
    assert blocked_result == 'Error: tool call was not approved.'
    assert len(calls) == 1

    # Mutating MCP tool executes only after explicit HITL confirmation
    confirmations = []

    async def approve_caller(event):
        confirmations.append(event)
        return True

    _, approved_result, *_ = await execute_tool_call(
        {},
        metadata,
        approve_caller,
        {'call_id': 'call-del-1', 'name': 'gw_delete_file', 'arguments': '{"file_id": "file-1"}'},
    )
    assert len(confirmations) == 1
    assert confirmations[0]['type'] == 'confirmation'
    assert approved_result == [{'type': 'text', 'text': 'deleted'}]
