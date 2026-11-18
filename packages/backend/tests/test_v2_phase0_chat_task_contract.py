"""Characterize existing chat-task reconnect DTOs with identity and Redis mocked."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from weai.routers import chat_tasks


@pytest.mark.asyncio
async def test_saved_chat_task_list_filters_by_current_chat_access_and_user(monkeypatch):
    chat = SimpleNamespace(id='chat-1', user_id='owner')
    visible = {'value': True}

    async def accessible(_chat_id, _user, permission):
        assert permission == 'write'
        return chat if visible['value'] else None

    async def task_ids(_redis, _chat_id):
        return ['owner-task', 'other-task']

    async def filtered(_chat, _user_id, ids):
        assert ids == ['owner-task', 'other-task']
        return ['owner-task']

    monkeypatch.setattr(chat_tasks, 'get_temporary_chat_session_id', lambda _id: None)
    monkeypatch.setattr(chat_tasks.Chats, 'get_accessible_chat_by_id', accessible)
    monkeypatch.setattr(chat_tasks, 'list_task_ids_by_item_id', task_ids)
    monkeypatch.setattr(chat_tasks.Chats, 'filter_task_ids_by_user_id', filtered)
    app = FastAPI()
    app.state.redis = object()
    app.include_router(chat_tasks.router)
    app.dependency_overrides[chat_tasks.get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        allowed = await client.get('/api/tasks/chat/chat-1')
        visible['value'] = False
        denied = await client.get('/api/tasks/chat/chat-1')
    assert allowed.status_code == 200 and allowed.json() == {'task_ids': ['owner-task']}
    assert denied.status_code == 200 and denied.json() == {'task_ids': []}


@pytest.mark.asyncio
async def test_temporary_chat_resume_returns_task_ids_and_streams_or_404(monkeypatch):
    accepted = {'value': True}

    async def register(*_args, **_kwargs):
        return accepted['value']

    async def task_ids(*_args):
        return ['task-1']

    async def streams(*_args):
        return [{'message_id': 'message-1', 'output': []}]

    monkeypatch.setattr(chat_tasks, 'register_temporary_chat_session', register)
    monkeypatch.setattr(chat_tasks, 'get_user_id_from_session_pool', lambda _id: 'owner')
    monkeypatch.setattr(chat_tasks, 'list_task_ids_by_item_id', task_ids)
    monkeypatch.setattr(chat_tasks, 'get_response_streams_by_chat_id', streams)
    app = FastAPI()
    app.state.redis = object()
    app.include_router(chat_tasks.router)
    app.dependency_overrides[chat_tasks.get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        allowed = await client.post('/api/tasks/chat/temp-chat/resume', json={'session_id': 'socket-new'})
        accepted['value'] = False
        denied = await client.post('/api/tasks/chat/temp-chat/resume', json={'session_id': 'socket-new'})
    assert allowed.status_code == 200
    assert allowed.json() == {'task_ids': ['task-1'], 'streams': [{'message_id': 'message-1', 'output': []}]}
    assert denied.status_code == 404
