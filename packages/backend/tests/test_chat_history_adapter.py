from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.history import load_messages_from_db


@pytest.mark.asyncio
async def test_loads_branch_history_and_replays_unedited_skill_expansion(monkeypatch):
    messages_map = {
        'user-1': {
            'id': 'user-1',
            'parentId': None,
            'role': 'user',
            'content': 'original request',
            'files': [{'id': 'file-1'}],
            'meta': {
                'skills_create': {
                    'original_text': 'original request',
                    'expanded_text': 'expanded request',
                }
            },
            'private_field': 'drop me',
        },
        'empty-assistant': {
            'id': 'empty-assistant',
            'parentId': 'user-1',
            'role': 'assistant',
            'content': '',
        },
        'tool-assistant': {
            'id': 'tool-assistant',
            'parentId': 'empty-assistant',
            'role': 'assistant',
            'content': '',
            'output': [{'type': 'function_call', 'call_id': 'call-1'}],
            'model': 'model-1',
            'private_field': 'drop me too',
        },
        'user-2': {
            'id': 'user-2',
            'parentId': 'tool-assistant',
            'role': 'user',
            'content': 'follow up',
        },
    }
    get_messages = AsyncMock(return_value=messages_map)
    monkeypatch.setattr('open_webui.utils.chat_runtime.history.Chats.get_messages_map_by_chat_id', get_messages)

    result = await load_messages_from_db('chat-1', 'user-2')

    get_messages.assert_awaited_once_with('chat-1')
    assert [message['id'] for message in result] == ['user-1', 'tool-assistant', 'user-2']
    assert result[0]['content'] == 'expanded request'
    assert result[0]['files'] == [{'id': 'file-1'}]
    assert result[1]['output'] == [{'type': 'function_call', 'call_id': 'call-1'}]
    assert all('private_field' not in message for message in result)


@pytest.mark.asyncio
async def test_does_not_replay_skill_expansion_after_user_edits(monkeypatch):
    messages_map = {
        'user-1': {
            'id': 'user-1',
            'role': 'user',
            'content': 'edited request',
            'meta': {
                'skills_create': {
                    'original_text': 'original request',
                    'expanded_text': 'expanded request',
                }
            },
        }
    }
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.history.Chats.get_messages_map_by_chat_id',
        AsyncMock(return_value=messages_map),
    )

    result = await load_messages_from_db('chat-1', 'user-1')

    assert result[0]['content'] == 'edited request'


@pytest.mark.asyncio
@pytest.mark.parametrize('messages_map', [{}, {'user-1': {'id': 'user-1', 'role': 'user'}}])
async def test_returns_none_when_chat_history_or_requested_message_is_missing(monkeypatch, messages_map):
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.history.Chats.get_messages_map_by_chat_id',
        AsyncMock(return_value=messages_map),
    )

    result = await load_messages_from_db('chat-1', 'missing')

    assert result is None


def test_middleware_keeps_legacy_history_adapter_imports():
    from open_webui.utils import middleware
    from open_webui.utils.chat_runtime.history import MESSAGE_REPLAY_KEYS

    assert middleware.load_messages_from_db is load_messages_from_db
    assert middleware.MESSAGE_REPLAY_KEYS == MESSAGE_REPLAY_KEYS
