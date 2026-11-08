from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware


@pytest.mark.asyncio
async def test_saved_chat_response_context_restores_deepcopied_assistant_message(monkeypatch):
    metadata = {
        'chat_id': 'saved-chat',
        'message_id': 'assistant-1',
        'assistant_message_id': 'assistant-1',
        'session_id': 'socket-1',
    }
    assistant_message = {'id': 'assistant-1', 'content': 'Previous', 'output': [{'text': 'Previous'}]}
    event_emitter = AsyncMock()
    event_caller = AsyncMock()
    get_event_emitter = AsyncMock(return_value=event_emitter)
    get_event_call = AsyncMock(return_value=event_caller)
    get_message = AsyncMock(return_value=assistant_message)
    monkeypatch.setattr(middleware, 'get_event_emitter', get_event_emitter)
    monkeypatch.setattr(middleware, 'get_event_call', get_event_call)
    monkeypatch.setattr(middleware.Chats, 'get_message_by_id_and_message_id', get_message)

    ctx = await middleware.build_chat_response_context(
        request=object(),
        form_data={'messages': [{'role': 'user', 'content': 'Continue'}]},
        user=object(),
        model={'id': 'model-1'},
        metadata=metadata,
        tasks={},
        events=[],
    )

    get_event_emitter.assert_awaited_once_with(metadata)
    get_event_call.assert_awaited_once_with(metadata)
    get_message.assert_awaited_once_with('saved-chat', 'assistant-1')
    assert ctx['event_emitter'] is event_emitter
    assert ctx['event_caller'] is event_caller
    assert ctx['assistant_message'] == assistant_message
    assert ctx['assistant_message'] is not assistant_message
    assert ctx['assistant_message']['output'] is not assistant_message['output']


@pytest.mark.asyncio
async def test_temporary_chat_response_context_uses_last_assistant_request_message(monkeypatch):
    metadata = {
        'chat_id': 'temporary:session-1',
        'message_id': 'assistant-1',
        'assistant_message_id': 'assistant-1',
    }
    form_data = {
        'messages': [
            {'role': 'user', 'content': 'Question'},
            {'role': 'assistant', 'id': 'assistant-1', 'content': 'Partial', 'output': [{'text': 'Partial'}]},
        ]
    }
    event_emitter = AsyncMock()
    get_event_emitter = AsyncMock(return_value=event_emitter)
    get_event_call = AsyncMock()
    get_message = AsyncMock()
    monkeypatch.setattr(middleware, 'get_event_emitter', get_event_emitter)
    monkeypatch.setattr(middleware, 'get_event_call', get_event_call)
    monkeypatch.setattr(middleware.Chats, 'get_message_by_id_and_message_id', get_message)

    ctx = await middleware.build_chat_response_context(
        request=object(),
        form_data=form_data,
        user=object(),
        model={'id': 'model-1'},
        metadata=metadata,
        tasks={},
        events=[],
    )

    get_event_emitter.assert_awaited_once_with(metadata)
    get_event_call.assert_not_awaited()
    get_message.assert_not_awaited()
    assert ctx['assistant_message'] == form_data['messages'][-1]
    assert ctx['assistant_message'] is not form_data['messages'][-1]


@pytest.mark.asyncio
async def test_event_handlers_require_chat_and_message_ids(monkeypatch):
    get_event_emitter = AsyncMock()
    get_event_call = AsyncMock()
    monkeypatch.setattr(middleware, 'get_event_emitter', get_event_emitter)
    monkeypatch.setattr(middleware, 'get_event_call', get_event_call)

    emitter, caller = await middleware.get_event_emitter_and_caller({'session_id': 'socket-1'})

    assert emitter is None
    assert caller is None
    get_event_emitter.assert_not_awaited()
    get_event_call.assert_not_awaited()
