"""Characterize existing Socket.IO envelopes before replacing internals for V2."""

from unittest.mock import AsyncMock

import pytest

from open_webui.socket import main as socket_main


@pytest.mark.asyncio
async def test_saved_chat_completion_emits_owner_and_shared_envelopes(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(socket_main.sio, 'emit', emit)
    emitter = await socket_main.get_event_emitter(
        {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1', 'session_id': 'session-1'},
        update_db=False,
    )
    event = {'type': 'chat:completion', 'data': {'content': 'Hello', 'done': True}}

    await emitter(event)

    assert emit.await_count == 2
    owner_call, shared_call = emit.await_args_list
    assert owner_call.args == (
        'events', {'chat_id': 'chat-1', 'message_id': 'message-1', 'data': event}
    )
    assert owner_call.kwargs['room'] == 'user:user-1'
    assert shared_call.args == (
        'events',
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'user_id': 'user-1',
            'shared': True,
            'data': event,
        },
    )
    assert shared_call.kwargs == {'room': 'chat:chat-1', 'skip_sid': 'session-1'}


@pytest.mark.asyncio
async def test_internal_notification_does_not_emit_to_chat(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(socket_main.sio, 'emit', emit)
    emitter = await socket_main.get_event_emitter(
        {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1', 'internal': True},
        update_db=False,
    )

    await emitter({'type': 'notification', 'data': {'message': 'hidden'}})

    emit.assert_not_awaited()
