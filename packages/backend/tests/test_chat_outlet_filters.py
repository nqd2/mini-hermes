from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware


def make_request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})))


@pytest.mark.asyncio
async def test_temporary_chat_runs_outlet_filter_and_emits_updated_messages(monkeypatch):
    event_emitter = AsyncMock()
    filtered_messages = [{'id': 'assistant-1', 'role': 'assistant', 'content': 'Filtered answer.'}]
    process_filters = AsyncMock(return_value=({'messages': filtered_messages}, None))
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[object()]))
    monkeypatch.setattr(middleware, 'get_sorted_filters', lambda model_id, models: [])
    monkeypatch.setattr(
        middleware,
        'process_pipeline_outlet_filter',
        AsyncMock(side_effect=lambda request, data, user, models: data),
    )
    monkeypatch.setattr(middleware, 'process_filter_functions', process_filters)

    await middleware.outlet_filter_handler(
        {
            'request': make_request(),
            'user': object(),
            'model': {'id': 'model-1'},
            'metadata': {'chat_id': 'temporary:session-1', 'message_id': 'assistant-1'},
            'form_data': {'messages': [{'role': 'user', 'content': 'Question?'}]},
            'assistant_message': {'content': 'Original answer.'},
            'event_emitter': event_emitter,
        }
    )

    filter_payload = process_filters.await_args.kwargs['form_data']
    assert filter_payload['messages'][-1]['content'] == 'Original answer.'
    event_emitter.assert_awaited_once_with({'type': 'chat:outlet', 'data': {'messages': filtered_messages}})


@pytest.mark.asyncio
async def test_saved_chat_persists_only_changed_outlet_messages(monkeypatch):
    messages_map = {
        'user-1': {'id': 'user-1', 'role': 'user', 'content': 'Question?'},
        'assistant-1': {'id': 'assistant-1', 'role': 'assistant', 'content': 'Original answer.'},
    }
    save_message = AsyncMock()
    event_emitter = AsyncMock()
    filtered_messages = [
        {'id': 'assistant-1', 'role': 'assistant', 'content': 'Filtered answer.'},
        {'id': 'user-1', 'role': 'user', 'content': 'Question?'},
    ]
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', True)
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[object()]))
    monkeypatch.setattr(middleware, 'get_sorted_filters', lambda model_id, models: [])
    monkeypatch.setattr(
        middleware,
        'process_pipeline_outlet_filter',
        AsyncMock(side_effect=lambda request, data, user, models: data),
    )
    monkeypatch.setattr(
        middleware,
        'process_filter_functions',
        AsyncMock(return_value=({'messages': filtered_messages}, None)),
    )
    monkeypatch.setattr(middleware.Chats, 'get_messages_map_by_chat_id', AsyncMock(return_value=messages_map))
    monkeypatch.setattr(middleware.Chats, 'upsert_message_to_chat_by_id_and_message_id', save_message)

    await middleware.outlet_filter_handler(
        {
            'request': make_request(),
            'user': object(),
            'model': {'id': 'model-1'},
            'metadata': {'chat_id': 'saved-chat', 'message_id': 'assistant-1'},
            'form_data': {'messages': []},
            'assistant_message': {'content': 'Original answer.'},
            'event_emitter': event_emitter,
        }
    )

    save_message.assert_awaited_once_with(
        'saved-chat',
        'assistant-1',
        {'originalContent': 'Original answer.', 'content': 'Filtered answer.'},
    )
    event_emitter.assert_awaited_once_with({'type': 'chat:outlet', 'data': {'messages': filtered_messages}})


@pytest.mark.asyncio
async def test_outlet_filters_skip_models_without_filter_configuration(monkeypatch):
    event_emitter = AsyncMock()
    pipeline_filter_lookup = AsyncMock()
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', False)
    monkeypatch.setattr(middleware, 'get_sorted_filters', lambda model_id, models: [])
    monkeypatch.setattr(middleware, 'process_pipeline_outlet_filter', pipeline_filter_lookup)

    await middleware.outlet_filter_handler(
        {
            'request': make_request(),
            'user': object(),
            'model': {'id': 'model-1'},
            'metadata': {'chat_id': 'temporary:session-1'},
            'form_data': {'messages': []},
            'assistant_message': {'content': 'Answer.'},
            'event_emitter': event_emitter,
        }
    )

    pipeline_filter_lookup.assert_not_awaited()
    event_emitter.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_outlet_filter_entrypoint_supplies_adapter_ports(monkeypatch):
    run_outlet_filters = AsyncMock()
    ctx = {'request': make_request(), 'user': object(), 'metadata': {}}
    monkeypatch.setattr(middleware, 'run_outlet_filters', run_outlet_filters)

    await middleware.outlet_filter_handler(ctx)

    run_outlet_filters.assert_awaited_once()
    assert run_outlet_filters.await_args.args[0] is ctx
