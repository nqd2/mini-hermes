from unittest.mock import AsyncMock

import pytest

from open_webui.constants import TASKS
from open_webui.utils import middleware
from open_webui.utils.chat_runtime import background_tasks


@pytest.mark.asyncio
async def test_temporary_chat_follow_ups_use_completed_assistant_message(monkeypatch):
    generate_follow_ups = AsyncMock(
        return_value={'choices': [{'message': {'content': '{"follow_ups": ["What next?"]}'}}]}
    )
    event_emitter = AsyncMock()
    review_memory = AsyncMock()
    monkeypatch.setattr(background_tasks, 'generate_follow_ups', generate_follow_ups)
    monkeypatch.setattr(background_tasks, 'review_memory_after_turn', review_memory)

    await middleware.background_tasks_handler(
        {
            'request': object(),
            'form_data': {'model': 'model-1', 'messages': [{'role': 'user', 'content': 'Explain this.'}]},
            'user': object(),
            'metadata': {'chat_id': 'temporary:session-1', 'message_id': 'assistant-1'},
            'tasks': {TASKS.FOLLOW_UP_GENERATION: True},
            'event_emitter': event_emitter,
            'assistant_message': {'content': 'The answer.'},
        }
    )

    generated_form = generate_follow_ups.await_args.args[1]
    assert generated_form['model'] == 'model-1'
    assert generated_form['messages'][-1] == {
        'content': 'The answer.',
        'role': 'assistant',
        'id': 'assistant-1',
    }
    event_emitter.assert_awaited_once_with({'type': 'chat:message:follow_ups', 'data': {'follow_ups': ['What next?']}})
    review_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_tasks_skip_when_saved_chat_was_deleted(monkeypatch):
    get_messages = AsyncMock(return_value={})
    generate_follow_ups = AsyncMock()
    review_memory = AsyncMock()
    monkeypatch.setattr(background_tasks.Chats, 'get_messages_map_by_chat_id', get_messages)
    monkeypatch.setattr(background_tasks, 'generate_follow_ups', generate_follow_ups)
    monkeypatch.setattr(background_tasks, 'review_memory_after_turn', review_memory)

    await middleware.background_tasks_handler(
        {
            'request': object(),
            'form_data': {'model': 'model-1', 'messages': [{'role': 'user', 'content': 'Explain this.'}]},
            'user': object(),
            'metadata': {'chat_id': 'saved-chat', 'message_id': 'assistant-1'},
            'tasks': {TASKS.FOLLOW_UP_GENERATION: True},
            'event_emitter': AsyncMock(),
        }
    )

    get_messages.assert_awaited_once_with('saved-chat')
    generate_follow_ups.assert_not_awaited()
    review_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_chat_persists_generated_follow_ups_title_and_tags(monkeypatch):
    messages_map = {
        'user-1': {'id': 'user-1', 'role': 'user', 'content': 'Summarize the report.', 'parentId': None},
        'assistant-1': {
            'id': 'assistant-1',
            'role': 'assistant',
            'content': 'Summary. <details>hidden</details> ![chart](chart.png)',
            'model': 'model-1',
            'parentId': 'user-1',
        },
    }
    generate_follow_ups = AsyncMock(
        return_value={'choices': [{'message': {'content': '{"follow_ups": ["What changed?"]}'}}]}
    )
    generate_title = AsyncMock(return_value={'choices': [{'message': {'content': '{"title": "Report"}'}}]})
    generate_tags = AsyncMock(return_value={'choices': [{'message': {'content': '{"tags": ["report"]}'}}]})
    save_follow_ups = AsyncMock()
    save_title = AsyncMock()
    save_tags = AsyncMock()
    event_emitter = AsyncMock()
    review_memory = AsyncMock()
    user = object()

    monkeypatch.setattr(background_tasks.Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=messages_map))
    monkeypatch.setattr(background_tasks.Chats, 'upsert_message_to_chat_by_id_and_message_id', save_follow_ups)
    monkeypatch.setattr(background_tasks.Chats, 'update_chat_title_by_id', save_title)
    monkeypatch.setattr(background_tasks.Chats, 'update_chat_tags_by_id', save_tags)
    monkeypatch.setattr(background_tasks, 'generate_follow_ups', generate_follow_ups)
    monkeypatch.setattr(background_tasks, 'generate_title', generate_title)
    monkeypatch.setattr(background_tasks, 'generate_chat_tags', generate_tags)
    monkeypatch.setattr(background_tasks, 'review_memory_after_turn', review_memory)

    await middleware.background_tasks_handler(
        {
            'request': object(),
            'form_data': {'model': 'model-1', 'messages': [{'role': 'user', 'content': 'Summarize the report.'}]},
            'user': user,
            'metadata': {'chat_id': 'saved-chat', 'message_id': 'assistant-1'},
            'tasks': {
                TASKS.FOLLOW_UP_GENERATION: True,
                TASKS.TITLE_GENERATION: True,
                TASKS.TAGS_GENERATION: True,
            },
            'event_emitter': event_emitter,
            'assistant_message': {'content': 'Summary.'},
        }
    )

    follow_up_messages = generate_follow_ups.await_args.args[1]['messages']
    assert follow_up_messages[-1]['content'] == 'Summary.'
    save_follow_ups.assert_awaited_once_with('saved-chat', 'assistant-1', {'followUps': ['What changed?']}, touch=False)
    save_title.assert_awaited_once_with('saved-chat', 'Report')
    save_tags.assert_awaited_once_with('saved-chat', ['report'], user)
    assert [call.args[0]['type'] for call in event_emitter.await_args_list] == [
        'chat:message:follow_ups',
        'chat:title',
        'chat:tags',
    ]
    review_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_title_result_does_not_create_title_for_existing_chat(monkeypatch):
    messages_map = {
        'user-1': {'id': 'user-1', 'role': 'user', 'content': 'First question.', 'parentId': None},
        'assistant-1': {'id': 'assistant-1', 'role': 'assistant', 'content': 'First answer.', 'parentId': 'user-1'},
        'user-2': {'id': 'user-2', 'role': 'user', 'content': 'Follow-up question.', 'parentId': 'assistant-1'},
        'assistant-2': {
            'id': 'assistant-2',
            'role': 'assistant',
            'content': 'Follow-up answer.',
            'model': 'model-1',
            'parentId': 'user-2',
        },
    }
    save_title = AsyncMock()
    monkeypatch.setattr(background_tasks.Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=messages_map))
    monkeypatch.setattr(background_tasks.Chats, 'update_chat_title_by_id', save_title)
    monkeypatch.setattr(background_tasks, 'generate_title', AsyncMock(return_value=None))
    monkeypatch.setattr(background_tasks, 'review_memory_after_turn', AsyncMock())

    await middleware.background_tasks_handler(
        {
            'request': object(),
            'form_data': {'model': 'model-1', 'messages': []},
            'user': object(),
            'metadata': {'chat_id': 'saved-chat', 'message_id': 'assistant-2'},
            'tasks': {TASKS.TITLE_GENERATION: True},
            'event_emitter': AsyncMock(),
        }
    )

    save_title.assert_not_awaited()


def test_middleware_keeps_post_turn_handler_compatibility_import():
    assert middleware.background_tasks_handler is background_tasks.background_tasks_handler
