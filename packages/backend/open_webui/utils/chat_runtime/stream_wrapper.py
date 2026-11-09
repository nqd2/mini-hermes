"""Adapt fallback provider streams to chat events, resource gates, and outlet filters."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from starlette.responses import StreamingResponse

from open_webui.utils.json_codec import JSONCodec


@dataclass
class StreamWrapperAdapters:
    api_outlet_filters_enabled: bool
    filter_context_factory: Callable
    process_filter_functions: Callable
    get_sorted_filters: Callable
    resource_gate_requires_fallback: Callable
    update_assistant_message_from_stream: Callable
    outlet_filter_handler: Callable
    log_model_usage: Callable
    convert_responses_stream_to_chat_completions: Callable
    logger: logging.Logger


@dataclass
class _StreamState:
    message: dict
    current_usage: dict | None = None
    response_stream: bool = False
    fallback_emitted: bool = False
    outlet_task: object | None = None


async def wrap_chat_completion_stream(response, ctx, events, filter_functions, extra_params, adapters):
    request = ctx['request']
    user = ctx['user']
    form_data = ctx['form_data']
    state = _StreamState(message={})

    async def stream_wrapper(original_generator):
        try:
            answer_gate = ctx.get('resource_answer_gate')
            gate_stream = bool(
                answer_gate and answer_gate.active and adapters.resource_gate_requires_fallback(answer_gate)
            )
            audit_enabled = bool(getattr(request.state, 'audit_request_id', None))
            outlet_enabled = _is_outlet_filter_enabled(adapters, request, ctx['model'], filter_functions)
            filter_context = adapters.filter_context_factory()

            async for event_chunk in _filter_initial_events(
                events, request, filter_context, filter_functions, extra_params, adapters
            ):
                yield event_chunk

            async for data in original_generator:
                data = await _apply_audit_and_stream_filters(
                    data,
                    state,
                    audit_enabled,
                    filter_functions,
                    request,
                    form_data,
                    filter_context,
                    extra_params,
                    adapters,
                )
                fallback, data = _apply_resource_gate(data, state, answer_gate, gate_stream, form_data)
                if fallback:
                    yield fallback
                if data:
                    await _schedule_outlet_filter(data, state, outlet_enabled, ctx, adapters)
                    yield data

            if gate_stream and not state.fallback_emitted:
                state.fallback_emitted = True
                yield _wrap_item(_fallback_chunk(answer_gate, state.response_stream, form_data))

            if state.outlet_task is not None:
                await asyncio.shield(state.outlet_task)
            elif outlet_enabled and state.message:
                ctx['assistant_message'] = state.message
                await adapters.outlet_filter_handler(ctx)
        except Exception as error:
            adapters.logger.exception('Chat completion stream failed mid-response: %s', error)
            yield f'\n\ndata: {JSONCodec.dumps({"error": {"message": "Chat completion stream failed"}})}\n\n'
            yield 'data: [DONE]\n\n'
        finally:
            adapters.log_model_usage(request, user, form_data['model'], state.current_usage)

    stream = stream_wrapper(response.body_iterator)
    converted = adapters.convert_responses_stream_to_chat_completions(stream)
    return StreamingResponse(converted, headers=dict(response.headers), background=response.background)


def _is_outlet_filter_enabled(adapters, request, model, filter_functions):
    if not adapters.api_outlet_filters_enabled:
        return False
    if filter_functions:
        return True
    try:
        return bool(
            (isinstance(model, dict) and 'pipeline' in model)
            or adapters.get_sorted_filters(
                model.get('id') if isinstance(model, dict) else model, request.app.state.MODELS
            )
        )
    except Exception:
        return True


async def _filter_initial_events(events, request, filter_context, filter_functions, extra_params, adapters):
    for event in events:
        event, _ = await adapters.process_filter_functions(
            request=request,
            filter_context=filter_context,
            filter_functions=filter_functions,
            filter_type='stream',
            form_data=event,
            extra_params=extra_params,
        )
        if event:
            yield _wrap_item(JSONCodec.dumps(event))


async def _apply_audit_and_stream_filters(
    data,
    state,
    audit_enabled,
    filter_functions,
    request,
    form_data,
    filter_context,
    extra_params,
    adapters,
):
    if not (filter_functions or audit_enabled):
        return data
    line = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
    if not isinstance(line, str) or not line.startswith('data:'):
        return data
    payload_text = line.removeprefix('data:').strip()
    if not payload_text or payload_text == '[DONE]':
        return data
    try:
        event = JSONCodec.loads(payload_text)
    except JSONCodec.JSONDecodeError:
        return data
    if not isinstance(event, dict):
        return data

    if audit_enabled:
        form_data['model'] = event.get('selected_model_id') or form_data['model']
        payload = event.get('response') if isinstance(event.get('response'), dict) else event
        usage = payload.get('usage') or {}
        timings = payload.get('timings') or {}
        if isinstance(usage, dict) and isinstance(timings, dict) and (usage or timings):
            state.current_usage = {**usage, **timings}
    if filter_functions:
        event, _ = await adapters.process_filter_functions(
            request=request,
            filter_context=filter_context,
            filter_functions=filter_functions,
            filter_type='stream',
            form_data=event,
            extra_params=extra_params,
        )
        return _wrap_item(JSONCodec.dumps(event)) if event else None
    return data


def _apply_resource_gate(data, state, answer_gate, gate_stream, form_data):
    if not gate_stream or not data:
        return None, data
    line = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
    if not isinstance(line, str) or not line.startswith('data:'):
        return None, data
    payload_text = line.removeprefix('data:').strip()
    if payload_text == '[DONE]':
        if state.fallback_emitted:
            return None, data
        state.fallback_emitted = True
        return _wrap_item(_fallback_chunk(answer_gate, state.response_stream, form_data)), data
    try:
        payload = JSONCodec.loads(payload_text)
    except JSONCodec.JSONDecodeError:
        return None, data
    if not isinstance(payload, dict):
        return None, data

    event_type = str(payload.get('type') or '')
    if event_type.startswith('response.'):
        state.response_stream = True
        if event_type in {'response.completed', 'response.incomplete'} and not state.fallback_emitted:
            state.fallback_emitted = True
            fallback = _wrap_item(_fallback_chunk(answer_gate, True, form_data))
        else:
            fallback = None
    else:
        fallback = None

    filtered = answer_gate.filter_provider_payload(payload)
    if filtered is None:
        return fallback, None
    return fallback, _wrap_item(JSONCodec.dumps(filtered))


async def _schedule_outlet_filter(data, state, outlet_enabled, ctx, adapters):
    if not outlet_enabled:
        return
    done = adapters.update_assistant_message_from_stream(state.message, data)
    if done and state.message and state.outlet_task is None:
        ctx['assistant_message'] = state.message
        state.outlet_task = asyncio.create_task(adapters.outlet_filter_handler(ctx))


def _fallback_chunk(answer_gate, responses_format, form_data):
    text = answer_gate.fallback_text_for_current_state()
    if responses_format:
        return JSONCodec.dumps(
            {
                'type': 'response.output_text.delta',
                'delta': text,
                'output_index': 0,
                'content_index': 0,
            }
        )
    return JSONCodec.dumps(
        {
            'id': f'chatcmpl-{uuid4().hex}',
            'object': 'chat.completion.chunk',
            'created': int(time.time()),
            'model': form_data.get('model', ''),
            'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}],
        }
    )


def _wrap_item(item):
    return f'data: {item}\n\n'
