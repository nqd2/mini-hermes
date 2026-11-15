"""Characterize the AgentRuntime replay HTTP DTO with a mocked runtime backend."""

import datetime as dt
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

main = importlib.import_module("open_webui.main")
from open_webui.agent_runtime.runtime import AgentRuntime


@pytest.fixture
def runtime_app(monkeypatch):
    app = FastAPI()
    app.add_api_route('/api/v1/agent/turns/{turn_id}/events', main.get_agent_turn_events, methods=['GET'])
    app.add_api_route('/api/v1/agent/turns/{turn_id}', main.get_agent_turn_status, methods=['GET'])
    app.add_api_route('/api/v1/agent/turns/{turn_id}/cancel', main.cancel_agent_turn, methods=['POST'])
    app.dependency_overrides[main.get_verified_user] = lambda: SimpleNamespace(id='owner-1')
    event = SimpleNamespace(id='event-3', sequence=3, event_type='client.chat:completion',
                            payload={'event': {'type': 'chat:completion', 'data': {'content': 'hello'}}},
                            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC), published_at=None)
    repo = SimpleNamespace(get_owned_turn=AsyncMock(return_value=SimpleNamespace(status='running')))
    runtime = SimpleNamespace(repository=repo, get_status=AsyncMock(return_value={'turn_id': 'turn-1', 'status': 'running'}),
                              cancel=AsyncMock(return_value={'turn_id': 'turn-1', 'status': 'cancel_requested'}),
                              stream=AsyncMock(return_value={
        'first_available_sequence': 1, 'latest_sequence': 3, 'next_after_sequence': 3,
        'has_more': False, 'gap_detected': True, 'snapshot_required': True,
        'snapshot': {'turn_id': 'turn-1', 'status': 'running'}, 'events': [event],
    }))
    monkeypatch.setattr(AgentRuntime, 'configured', lambda: runtime)
    return app, runtime


@pytest.mark.asyncio
async def test_replay_route_preserves_gap_snapshot_and_event_shape(runtime_app):
    app, runtime = runtime_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/agent/turns/turn-1/events', params={'after_sequence': 1, 'limit': 20})
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in ('turn_id', 'status', 'latest_sequence', 'next_after_sequence',
                                      'gap_detected', 'snapshot_required')} == {
        'turn_id': 'turn-1', 'status': 'running', 'latest_sequence': 3, 'next_after_sequence': 3,
        'gap_detected': True, 'snapshot_required': True,
    }
    assert body['snapshot'] == {'turn_id': 'turn-1', 'status': 'running'}
    assert body['events'] == [{
        'id': 'event-3', 'sequence': 3, 'type': 'client.chat:completion',
        'payload': {'event': {'type': 'chat:completion', 'data': {'content': 'hello'}}},
        'created_at': '2026-01-01T00:00:00+00:00', 'published_at': None,
    }]
    runtime.stream.assert_awaited_once_with('turn-1', owner_id='owner-1', after_sequence=1, limit=20)


@pytest.mark.asyncio
async def test_replay_route_does_not_call_stream_for_unowned_turn(runtime_app):
    app, runtime = runtime_app
    runtime.repository.get_owned_turn.return_value = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/agent/turns/foreign/events')
    assert response.status_code == 404
    assert 'detail' in response.json()
    runtime.stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_and_cancel_routes_forward_authenticated_owner(runtime_app):
    app, runtime = runtime_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        status = await client.get('/api/v1/agent/turns/turn-1')
        cancelled = await client.post('/api/v1/agent/turns/turn-1/cancel')
    assert status.status_code == 200 and status.json() == {'turn_id': 'turn-1', 'status': 'running'}
    assert cancelled.status_code == 200 and cancelled.json() == {'turn_id': 'turn-1', 'status': 'cancel_requested'}
    runtime.get_status.assert_awaited_once_with('turn-1', owner_id='owner-1')
    runtime.cancel.assert_awaited_once_with('turn-1', owner_id='owner-1')
