"""Saved agent questions remain answerable after reconnect without duplicate resumes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.utils import tool_approval


@pytest.fixture
def pending_question(monkeypatch):
    message = {
        'role': 'assistant',
        'user_id': 'owner-1',
        'output': [{
            'type': 'function_call', 'name': 'ask_user', 'call_id': 'call-1', 'status': 'pending',
            'arguments': '{"questions":[{"id":"choice","header":"Format","question":"Which format?",'
                         '"options":[{"label":"DOCX","description":"Word file"},'
                         '{"label":"PDF","description":"Portable file"}]}]}',
        }],
    }
    chat = SimpleNamespace(user_id='owner-1')
    monkeypatch.setattr(tool_approval.Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=chat))

    @asynccontextmanager
    async def edit_message_output(chat_id, message_id):
        assert (chat_id, message_id) == ('chat-1', 'assistant-1')
        yield message

    monkeypatch.setattr(tool_approval.Chats, 'edit_message_output', edit_message_output)
    monkeypatch.setattr(tool_approval, 'get_event_emitter', AsyncMock(return_value=None))
    return message


@pytest.mark.asyncio
async def test_same_answer_retry_does_not_add_second_continuation(pending_question):
    user = SimpleNamespace(id='owner-1')
    form = tool_approval.ResolveToolCallForm(
        call_id='call-1', action='answer',
        answers={'choice': {'type': 'option', 'option_index': 0, 'label': 'DOCX', 'description': 'Word file'}},
    )
    first = await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, user)
    second = await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, user)

    assert first['resume'] == 'model'
    assert second['resume'] is None
    assert len([item for item in pending_question['output'] if item['type'] == 'function_call_output']) == 1
    assert len([item for item in pending_question['output'] if item['type'] == 'message']) == 1


@pytest.mark.asyncio
async def test_different_answer_retry_is_rejected(pending_question):
    user = SimpleNamespace(id='owner-1')
    first = tool_approval.ResolveToolCallForm(call_id='call-1', action='answer', answers={
        'choice': {'type': 'option', 'option_index': 0, 'label': 'DOCX', 'description': 'Word file'},
    })
    second = tool_approval.ResolveToolCallForm(call_id='call-1', action='answer', answers={
        'choice': {'type': 'option', 'option_index': 1, 'label': 'PDF', 'description': 'Portable file'},
    })
    await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', first, user)
    with pytest.raises(HTTPException) as error:
        await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', second, user)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_same_rejection_retry_does_not_add_second_continuation(pending_question):
    user = SimpleNamespace(id='owner-1')
    form = tool_approval.ResolveToolCallForm(call_id='call-1', action='reject')
    first = await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, user)
    second = await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, user)

    assert first['resume'] == 'model'
    assert second['resume'] is None
    assert len([item for item in pending_question['output'] if item['type'] == 'function_call_output']) == 1
    assert len([item for item in pending_question['output'] if item['type'] == 'message']) == 1


@pytest.mark.asyncio
async def test_other_user_cannot_answer_saved_question(pending_question):
    form = tool_approval.ResolveToolCallForm(call_id='call-1', action='answer', answers={
        'choice': {'type': 'option', 'option_index': 0, 'label': 'DOCX', 'description': 'Word file'},
    })
    with pytest.raises(HTTPException) as error:
        await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, SimpleNamespace(id='other-user'))
    assert error.value.status_code == 403
    assert pending_question['output'][0]['status'] == 'pending'


@pytest.mark.asyncio
async def test_saved_question_rejects_browser_timeout(pending_question):
    form = tool_approval.ResolveToolCallForm(call_id='call-1', action='answer', timed_out=True)
    with pytest.raises(HTTPException) as error:
        await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, SimpleNamespace(id='owner-1'))
    assert error.value.status_code == 400
    assert pending_question['output'][0]['status'] == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('answers', [
    {},
    {'other_id': {'type': 'other', 'text': 'DOCX'}},
    {'choice': {'type': 'option', 'option_index': 9, 'label': 'DOCX', 'description': 'Word file'}},
    {'choice': {'type': 'option', 'option_index': 0, 'label': 'PDF', 'description': 'Portable file'}},
    {'choice': {'type': 'other', 'text': '   '}},
])
async def test_question_rejects_missing_or_forged_answers(pending_question, answers):
    form = tool_approval.ResolveToolCallForm(call_id='call-1', action='answer', answers=answers)
    with pytest.raises(HTTPException) as error:
        await tool_approval.resolve_tool_call_output('chat-1', 'assistant-1', form, SimpleNamespace(id='owner-1'))
    assert error.value.status_code == 400
    assert pending_question['output'][0]['status'] == 'pending'
