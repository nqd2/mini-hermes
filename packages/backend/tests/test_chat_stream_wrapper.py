from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import StreamingResponse

from open_webui.utils.chat_runtime.stream_wrapper import StreamWrapperAdapters, wrap_chat_completion_stream
from open_webui.utils.json_codec import JSONCodec


def _adapters(**overrides):
    async def passthrough_filter(**kwargs):
        return kwargs['form_data'], {}

    values = {
        'api_outlet_filters_enabled': False,
        'filter_context_factory': object,
        'process_filter_functions': AsyncMock(side_effect=passthrough_filter),
        'get_sorted_filters': Mock(return_value=[]),
        'resource_gate_requires_fallback': Mock(return_value=False),
        'update_assistant_message_from_stream': Mock(return_value=False),
        'outlet_filter_handler': AsyncMock(),
        'log_model_usage': Mock(),
        'convert_responses_stream_to_chat_completions': lambda stream: stream,
        'logger': Mock(),
    }
    values.update(overrides)
    return StreamWrapperAdapters(**values)


async def _chunks(*chunks):
    for chunk in chunks:
        yield chunk


def _context(*, audit_request_id=None, answer_gate=None):
    request = SimpleNamespace(
        state=SimpleNamespace(audit_request_id=audit_request_id),
        app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
    )
    user = object()
    form_data = {'model': 'requested-model'}
    model = {'id': 'requested-model'}
    ctx = {'request': request, 'user': user, 'model': model, 'form_data': form_data}
    if answer_gate is not None:
        ctx['resource_answer_gate'] = answer_gate
    return request, user, form_data, ctx


@pytest.mark.asyncio
async def test_wraps_initial_events_then_preserves_raw_sse_chunks():
    request, user, form_data, ctx = _context()
    status = {'type': 'status', 'data': {'description': 'working'}}
    provider_chunk = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    done_chunk = 'data: [DONE]\n\n'
    response = SimpleNamespace(
        body_iterator=_chunks(provider_chunk, done_chunk),
        headers={'content-type': 'text/event-stream'},
        background=None,
    )
    adapters = _adapters()

    result = await wrap_chat_completion_stream(response, ctx, [status], [], {}, adapters)
    chunks = [chunk async for chunk in result.body_iterator]

    assert isinstance(result, StreamingResponse)
    assert chunks[0] == f'data: {JSONCodec.dumps(status)}\n\n'
    assert chunks[1:] == [provider_chunk, done_chunk]
    adapters.process_filter_functions.assert_awaited_once()
    adapters.log_model_usage.assert_called_once_with(request, user, 'requested-model', None)
    assert form_data['model'] == 'requested-model'


@pytest.mark.asyncio
async def test_resource_answer_gate_drops_provider_claim_and_emits_fallback_before_done():
    class Gate:
        active = True
        metadata = {}

        def fallback_text_for_current_state(self):
            return 'Need more verified evidence.'

        def filter_provider_payload(self, _payload):
            return None

    request, user, _form_data, ctx = _context(answer_gate=Gate())
    response = SimpleNamespace(
        body_iterator=_chunks(
            'data: {"choices":[{"delta":{"content":"unverified claim"}}]}\n\n',
            'data: [DONE]\n\n',
        ),
        headers={},
        background=None,
    )
    resource_gate = Mock(return_value=True)
    adapters = _adapters(resource_gate_requires_fallback=resource_gate)

    result = await wrap_chat_completion_stream(response, ctx, [], [], {}, adapters)
    chunks = [chunk async for chunk in result.body_iterator]

    assert resource_gate.call_args.args == (ctx['resource_answer_gate'],)
    fallback = JSONCodec.loads(chunks[0].removeprefix('data:').strip())
    assert fallback['choices'][0]['delta']['content'] == 'Need more verified evidence.'
    assert chunks[-1] == 'data: [DONE]\n\n'
    adapters.log_model_usage.assert_called_once_with(request, user, 'requested-model', None)


@pytest.mark.asyncio
async def test_audit_stream_updates_selected_model_and_usage_before_logging():
    request, user, form_data, ctx = _context(audit_request_id='audit-1')
    chunk = 'data: {"selected_model_id":"routed-model","usage":{"total_tokens":7},"timings":{"latency":12}}\n\n'
    response = SimpleNamespace(body_iterator=_chunks(chunk), headers={}, background=None)
    adapters = _adapters()

    result = await wrap_chat_completion_stream(response, ctx, [], [], {}, adapters)
    assert [item async for item in result.body_iterator] == [chunk]

    assert form_data['model'] == 'routed-model'
    adapters.log_model_usage.assert_called_once_with(
        request,
        user,
        'routed-model',
        {'total_tokens': 7, 'latency': 12},
    )
