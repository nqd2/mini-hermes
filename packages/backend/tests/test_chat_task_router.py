import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_chat_task_routes_keep_existing_paths_methods_and_authentication():
    from open_webui.routers import chat_tasks
    from open_webui.utils.auth import get_admin_user, get_verified_user

    actual = {(route.path, method): route for route in chat_tasks.router.routes for method in route.methods}
    expected = {
        ('/api/tasks/stop/{task_id}', 'POST'): get_admin_user,
        ('/api/tasks', 'GET'): get_admin_user,
        ('/api/tasks/chat/{chat_id:path}', 'GET'): get_verified_user,
        ('/api/tasks/chat/{chat_id:path}/resume', 'POST'): get_verified_user,
        ('/api/tasks/chat/{chat_id:path}/stop', 'POST'): get_verified_user,
    }

    assert set(actual) == set(expected)
    for key, dependency in expected.items():
        assert dependency in [item.call for item in actual[key].dependant.dependencies]


def test_main_registers_chat_task_routes_from_the_router_module():
    main = importlib.import_module('open_webui.main')

    route_paths = {
        '/api/tasks/stop/{task_id}',
        '/api/tasks',
        '/api/tasks/chat/{chat_id:path}',
        '/api/tasks/chat/{chat_id:path}/resume',
        '/api/tasks/chat/{chat_id:path}/stop',
    }
    routes = [route for route in main.app.routes if getattr(route, 'path', None) in route_paths]

    assert {route.path for route in routes} == route_paths
    assert all(route.endpoint.__module__ == 'open_webui.routers.chat_tasks' for route in routes)


def test_admin_stop_preserves_not_found_response_for_unknown_task(monkeypatch):
    from open_webui.routers import chat_tasks

    async def stop_task(_redis, _task_id):
        raise ValueError('Task not found')

    monkeypatch.setattr(chat_tasks, 'stop_task', stop_task)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=object())))

    with pytest.raises(HTTPException) as error:
        asyncio.run(chat_tasks.stop_task_endpoint(request, 'missing', user=object()))

    assert error.value.status_code == 404
    assert error.value.detail == 'Task not found'


def test_pending_approval_finalization_only_updates_open_assistant_calls(monkeypatch):
    from open_webui.routers import chat_tasks

    writes = []
    emitted = []
    messages = {
        'owned': {
            'user_id': 'user-1',
            'role': 'assistant',
            'done': False,
            'output': [
                {'type': 'function_call', 'status': 'requires_approval', 'approved': True},
                {'type': 'function_call', 'status': 'completed'},
            ],
        },
        'other-user': {
            'user_id': 'user-2',
            'role': 'assistant',
            'done': False,
            'output': [{'type': 'function_call', 'status': 'pending'}],
        },
        'already-done': {
            'user_id': 'user-1',
            'role': 'assistant',
            'done': True,
            'output': [{'type': 'function_call', 'status': 'pending'}],
        },
    }

    async def get_messages_map(_chat_id):
        return messages

    async def upsert(_chat_id, message_id, patch, *, touch):
        writes.append((message_id, patch, touch))

    async def get_emitter(_info, *, update_db):
        assert update_db is False

        async def emit(event):
            emitted.append(event)

        return emit

    monkeypatch.setattr(chat_tasks.Chats, 'get_messages_map_by_chat_id', get_messages_map)
    monkeypatch.setattr(chat_tasks.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    monkeypatch.setattr(chat_tasks, 'get_event_emitter', get_emitter)

    finalized = asyncio.run(
        chat_tasks.finalize_pending_approval_messages(
            'chat-1', SimpleNamespace(user_id='user-1'), SimpleNamespace(id='user-1')
        )
    )

    assert finalized is True
    assert writes == [
        (
            'owned',
            {
                'done': True,
                'output': [
                    {'type': 'function_call', 'status': 'rejected'},
                    {'type': 'function_call', 'status': 'completed'},
                ],
            },
            False,
        )
    ]
    assert [event['type'] for event in emitted] == [
        'chat:completion',
        'chat:tasks:cancel',
        'chat:active',
    ]
