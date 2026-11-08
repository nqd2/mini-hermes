from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware


def _context(event_emitter, *, continuing=False):
    metadata = {'chat_id': 'temporary:session-1', 'message_id': 'assistant-1'}
    if continuing:
        metadata['assistant_message_id'] = 'assistant-1'
    return {
        'request': object(),
        'user': object(),
        'metadata': metadata,
        'form_data': {'model': 'model-1'},
        'events': [],
        'event_emitter': event_emitter,
        'assistant_message': (
            {
                'id': 'assistant-1',
                'content': 'Previously.',
                'output': [
                    {
                        'type': 'message',
                        'id': 'previous-output-id',
                        'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'Previously.'}],
                    }
                ],
            }
            if continuing
            else None
        ),
    }


def _disable_post_turn_work(monkeypatch):
    monkeypatch.setattr(middleware, 'ENABLE_API_OUTLET_FILTERS', False)
    monkeypatch.setattr(middleware, 'log_model_usage', lambda *args: None)
    monkeypatch.setattr(middleware, 'publish_chat_finished_event', AsyncMock())
    monkeypatch.setattr(middleware, 'outlet_filter_handler', AsyncMock())
    monkeypatch.setattr(middleware, 'background_tasks_handler', AsyncMock())


@pytest.mark.asyncio
async def test_non_streaming_response_builds_reasoning_and_message_output(monkeypatch):
    event_emitter = AsyncMock()
    _disable_post_turn_work(monkeypatch)
    response = {
        'choices': [
            {
                'message': {'content': 'Answer.', 'reasoning_content': 'Reasoning.'},
                'finish_reason': 'stop',
            }
        ]
    }

    await middleware.non_streaming_chat_response_handler(response, _context(event_emitter))

    completion = event_emitter.await_args_list[-1].args[0]
    assert completion['type'] == 'chat:completion'
    output = completion['data']['output']
    assert [item['type'] for item in output] == ['reasoning', 'message']
    assert output[0]['content'] == [{'type': 'output_text', 'text': 'Reasoning.'}]
    assert output[1]['content'] == [{'type': 'output_text', 'text': 'Answer.'}]


@pytest.mark.asyncio
async def test_non_streaming_continuation_merges_message_content_and_keeps_original_id(monkeypatch):
    event_emitter = AsyncMock()
    _disable_post_turn_work(monkeypatch)
    response = {'choices': [{'message': {'content': ' more'}, 'finish_reason': 'stop'}]}
    ctx = _context(event_emitter, continuing=True)

    await middleware.non_streaming_chat_response_handler(response, ctx)

    completion = event_emitter.await_args.args[0]
    assert completion['type'] == 'chat:completion'
    output = completion['data']['output']
    assert len(output) == 1
    assert output[0]['id'] == 'previous-output-id'
    assert output[0]['content'] == [
        {'type': 'output_text', 'text': 'Previously.'},
        {'type': 'output_text', 'text': ' more'},
    ]
    assert ctx['assistant_message']['content'] == 'Previously. more'


@pytest.mark.asyncio
async def test_non_streaming_provider_error_is_persisted_and_emitted(monkeypatch):
    event_emitter = AsyncMock()
    _disable_post_turn_work(monkeypatch)
    ctx = _context(event_emitter)
    ctx['metadata'].update({'chat_id': 'saved-chat', 'message_id': 'assistant-1'})
    save_message = AsyncMock()
    monkeypatch.setattr(middleware.Chats, 'upsert_message_to_chat_by_id_and_message_id', save_message)

    await middleware.non_streaming_chat_response_handler(
        {'error': {'detail': 'Provider request rejected'}},
        ctx,
    )

    save_message.assert_awaited_once()
    saved_error = save_message.await_args.args[2]
    assert saved_error['error'] == {'content': 'Provider request rejected'}
    assert saved_error['done'] is True
    assert 'response_ended_at' in saved_error['meta']
    event_emitter.assert_awaited_once_with(
        {
            'type': 'chat:message:error',
            'data': {'error': {'content': 'Provider request rejected'}, 'done': True},
        }
    )
