from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.utils import middleware
from open_webui.utils.chat_runtime import tool_resumption


def _message(status='pending'):
    return {
        'done': False,
        'model': 'model-1',
        'output': [
            {
                'type': 'function_call',
                'id': 'call-1',
                'name': 'echo',
                'arguments': '{}',
                'status': status,
                'approved': True,
            }
        ],
    }


def _install_message_store(monkeypatch, message):
    @asynccontextmanager
    async def edit_message_output(_chat_id, _message_id):
        yield message

    monkeypatch.setattr(middleware.Chats, 'edit_message_output', staticmethod(edit_message_output))


def _install_preparation(monkeypatch):
    monkeypatch.setattr(middleware, 'get_event_emitter_and_caller', AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(tool_resumption, 'process_messages_with_output', lambda messages, **_kwargs: messages)
    monkeypatch.setattr(tool_resumption, 'get_reasoning_format', lambda _model: None)
    monkeypatch.setattr(
        middleware,
        'convert_url_images_to_base64',
        AsyncMock(side_effect=lambda form_data, user=None: form_data),
    )
    monkeypatch.setattr(tool_resumption, 'normalize_messages_for_model', lambda _form_data: None)
    monkeypatch.setattr(tool_resumption, 'ENABLE_FUNCTIONS', False)


def _request_data(message):
    return {
        'request': object(),
        'form_data': {'messages': [{'role': 'user', 'content': 'run the approved tool'}]},
        'user': object(),
        'model': {'id': 'model-1', 'info': {'meta': {'capabilities': {'citations': False}}}},
        'metadata': {
            'chat_id': 'chat-1',
            'message_id': 'assistant-1',
            'params': {'tool_approval_mode': 'ask'},
        },
        'message': message,
    }


@pytest.mark.asyncio
async def test_resumes_approved_call_and_persists_result_and_continuation(monkeypatch):
    message = _message()
    _install_message_store(monkeypatch, message)
    _install_preparation(monkeypatch)
    emitted = []

    async def emit(event):
        emitted.append(event)

    monkeypatch.setattr(middleware, 'get_event_emitter_and_caller', AsyncMock(return_value=(emit, None)))
    monkeypatch.setattr(
        tool_resumption,
        'execute_tool_call',
        AsyncMock(return_value=({}, 'ok', {'type': 'builtin', 'tool_id': 'echo'}, 'builtin', False)),
    )
    monkeypatch.setattr(tool_resumption, 'process_tool_result', AsyncMock(return_value=('ok', [], [])))
    monkeypatch.setattr(middleware, 'terminal_event_handler', AsyncMock())

    paused = await middleware.resume_tool_calls(**_request_data(message))

    assert paused is False
    assert message['done'] is False
    assert message['output'][0]['status'] == 'completed'
    assert message['output'][1]['type'] == 'function_call_output'
    assert message['output'][1]['call_id'] == 'call-1'
    assert message['output'][1]['output'] == [{'type': 'input_text', 'text': 'ok'}]
    assert message['output'][2]['role'] == 'assistant'
    assert emitted == [{'type': 'chat:completion', 'data': {'done': False, 'output': message['output']}}]


@pytest.mark.asyncio
async def test_existing_in_progress_call_stays_paused_without_reexecution(monkeypatch):
    message = _message(status='in_progress')
    _install_message_store(monkeypatch, message)
    _install_preparation(monkeypatch)
    execute = AsyncMock()
    monkeypatch.setattr(tool_resumption, 'execute_tool_call', execute)

    paused = await middleware.resume_tool_calls(**_request_data(message))

    assert paused is True
    execute.assert_not_awaited()
    assert message['output'][0]['status'] == 'in_progress'


@pytest.mark.asyncio
async def test_interrupted_execution_is_marked_incomplete_before_reraising(monkeypatch):
    message = _message()
    _install_message_store(monkeypatch, message)
    _install_preparation(monkeypatch)
    monkeypatch.setattr(tool_resumption, 'execute_tool_call', AsyncMock(side_effect=RuntimeError('worker interrupted')))

    with pytest.raises(RuntimeError, match='worker interrupted'):
        await middleware.resume_tool_calls(**_request_data(message))

    assert message['done'] is True
    assert message['output'][0]['status'] == 'incomplete'
    assert message['output'][1]['status'] == 'incomplete'
    assert message['output'][1]['output'] == [
        {'type': 'input_text', 'text': 'Error: tool execution was interrupted; its outcome is unknown.'}
    ]


@pytest.mark.asyncio
async def test_request_filter_rejection_happens_before_tool_claim_or_execution(monkeypatch):
    message = _message()
    _install_message_store(monkeypatch, message)
    _install_preparation(monkeypatch)
    execute = AsyncMock()
    run_filter = AsyncMock(side_effect=HTTPException(status_code=403, detail='request rejected'))
    monkeypatch.setattr(tool_resumption, 'execute_tool_call', execute)
    monkeypatch.setattr(tool_resumption, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(tool_resumption, 'get_filter_functions', AsyncMock(return_value=[object()]))
    monkeypatch.setattr(tool_resumption, 'get_filter_context', lambda _request: object())
    monkeypatch.setattr(tool_resumption, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(tool_resumption, 'process_filter_functions', run_filter)

    with pytest.raises(HTTPException) as error:
        await middleware.resume_tool_calls(**_request_data(message))

    assert error.value.status_code == 403
    execute.assert_not_awaited()
    run_filter.assert_awaited_once()
    assert message['output'][0]['status'] == 'pending'
