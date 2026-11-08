"""Application workflow for non-streaming chat responses."""

import copy
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_webui.utils.chat_runtime.completion_output import build_non_streaming_output, merge_continuing_output
from open_webui.utils.chat_runtime.resource_execution import resource_task_state_metadata
from open_webui.utils.chat_runtime.stream_state import (
    build_response_object,
    get_response_data,
    merge_events_into_response,
    output_id,
)
from open_webui.utils.misc import get_output_text
from open_webui.utils.response import merge_usage, normalize_usage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NonStreamingResponseAdapters:
    """Open WebUI services used by the non-streaming response workflow."""

    log_model_usage: Callable[..., Any]
    is_saved_chat_id: Callable[[str | None], bool]
    upsert_message: Callable[..., Awaitable[Any]]
    get_chat_title: Callable[[str], Awaitable[str]]
    publish_finished_event: Callable[..., Awaitable[Any]]
    publish_failed_event: Callable[..., Awaitable[Any]]
    outlet_filter_handler: Callable[[dict], Awaitable[Any]]
    background_tasks_handler: Callable[[dict], Awaitable[Any]]
    api_outlet_filters_enabled: bool


async def _handle_provider_error(response_data, metadata, event_emitter, save_to_chat, adapters):
    if 'error' not in response_data:
        return

    error = response_data.get('error')
    if isinstance(error, dict):
        error = error.get('detail', error)
    else:
        error = str(error)

    log.error('Provider returned error (non-streaming): %s', error)
    if save_to_chat:
        await adapters.upsert_message(
            metadata['chat_id'],
            metadata['message_id'],
            {
                'error': {'content': error},
                'done': True,
                'meta': {
                    'response_ended_at': time.time(),
                    **resource_task_state_metadata(metadata),
                },
            },
        )
    if isinstance(error, str) or isinstance(error, dict):
        await event_emitter(
            {
                'type': 'chat:message:error',
                'data': {'error': {'content': error}, 'done': True},
            }
        )


async def _save_completed_message(metadata, response_output, usage, save_to_chat, adapters):
    if not save_to_chat:
        return
    await adapters.upsert_message(
        metadata['chat_id'],
        metadata['message_id'],
        {
            'done': True,
            'meta': {
                'response_ended_at': time.time(),
                **resource_task_state_metadata(metadata),
            },
            'role': 'assistant',
            'output': response_output,
            **({'usage': usage} if usage else {}),
        },
    )


async def _run_post_completion_actions(
    ctx, request, user, metadata, title, content, response_output, usage, finish_reason, adapters
):
    await adapters.publish_finished_event(request, user, metadata, title, content, response_output)
    ctx['assistant_message'] = {
        'content': content,
        'output': response_output,
        **({'usage': usage} if usage else {}),
        **({'finish_reason': finish_reason} if finish_reason else {}),
    }
    await adapters.outlet_filter_handler(ctx)
    await adapters.background_tasks_handler(ctx)


def _apply_answer_gate(response_data, choices, response_output, content, answer_gate):
    if not answer_gate:
        return response_output, content

    response_output = answer_gate.present(response_output, final=True)
    if answer_gate.active:
        content = get_output_text(response_output)
        response_data['output'] = response_output
        response_data['choices'] = copy.deepcopy(choices)
        if response_data['choices']:
            choice_message = response_data['choices'][0].setdefault('message', {})
            choice_message['content'] = content
            choice_message.pop('reasoning_content', None)
            choice_message.pop('reasoning', None)
    return response_output, content


def _should_complete_turn(response_data, choices, content, response_output, tool_calls, continuing):
    if continuing:
        return 'error' not in response_data
    return bool(choices and (content or response_output or tool_calls))


async def _complete_emitted_turn(
    ctx, response_data, choices, content, response_output, continuing, save_to_chat, adapters
):
    tool_calls = choices[0].get('message', {}).get('tool_calls') if choices else None
    if not _should_complete_turn(response_data, choices, content, response_output, tool_calls, continuing):
        return

    event_emitter = ctx['event_emitter']
    if not continuing:
        await event_emitter({'type': 'chat:completion', 'data': response_data})

    request = ctx['request']
    user = ctx['user']
    metadata = ctx['metadata']
    title = await adapters.get_chat_title(metadata['chat_id']) if save_to_chat else ''
    response_output = build_non_streaming_output(response_output, choices, content)
    if continuing:
        response_output = merge_continuing_output(ctx.get('assistant_message') or {}, response_output)
        content = get_output_text(response_output)

    response_output, content = _apply_answer_gate(
        response_data,
        choices,
        response_output,
        content,
        ctx.get('resource_answer_gate'),
    )
    await event_emitter(
        {
            'type': 'chat:completion',
            'data': {
                **(response_data if continuing else {}),
                'done': True,
                'output': response_output,
                'title': title,
            },
        }
    )

    usage = normalize_usage(response_data.get('usage', {}) or {})
    if continuing:
        usage = merge_usage((ctx.get('assistant_message') or {}).get('usage'), usage or {'incomplete': True})
    finish_reason = choices[0].get('finish_reason') if choices else None
    await _save_completed_message(metadata, response_output, usage, save_to_chat, adapters)
    await _run_post_completion_actions(
        ctx,
        request,
        user,
        metadata,
        title,
        content,
        response_output,
        usage,
        finish_reason,
        adapters,
    )


async def _handle_response_without_event_emitter(response, response_data, ctx, adapters):
    answer_gate = ctx.get('resource_answer_gate')
    if answer_gate and answer_gate.active:
        choices = response_data.get('choices', [])
        output = response_data.get('output')
        if not isinstance(output, list):
            message = choices[0].get('message', {}) if choices else {}
            output = [
                {
                    'type': 'message',
                    'id': output_id('msg'),
                    'status': 'completed',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': message.get('content') or ''}],
                }
            ]
        output, content = _apply_answer_gate(response_data, choices, output, '', answer_gate)

    choices = response_data.get('choices', [])
    output = response_data.get('output')
    content = choices[0].get('message', {}).get('content') if choices else ''
    if adapters.api_outlet_filters_enabled and (content or output):
        usage = normalize_usage(response_data.get('usage', {}) or {})
        finish_reason = choices[0].get('finish_reason') if choices else None
        ctx['assistant_message'] = {
            **({'content': content} if content else {}),
            **({'output': output} if output else {}),
            **({'usage': usage} if usage else {}),
            **({'finish_reason': finish_reason} if finish_reason else {}),
        }
        await adapters.outlet_filter_handler(ctx)

    if isinstance(response, dict):
        return merge_events_into_response(response_data, ctx['events'])
    return response


async def handle_non_streaming_response(response, ctx, adapters: NonStreamingResponseAdapters):
    request = ctx['request']
    user = ctx['user']
    metadata = ctx['metadata']
    events = ctx['events']
    event_emitter = ctx['event_emitter']

    response, response_data = get_response_data(response)
    if response_data is None:
        return response

    usage = response_data.get('usage') or {}
    timings = response_data.get('timings') or {}
    if isinstance(usage, dict) and isinstance(timings, dict):
        adapters.log_model_usage(
            request,
            user,
            response_data.get('selected_model_id') or ctx['form_data']['model'],
            {**usage, **timings},
        )

    chat_id = metadata.get('chat_id') or ''
    save_to_chat = adapters.is_saved_chat_id(chat_id)
    continuing = bool(metadata.get('assistant_message_id'))
    if event_emitter:
        try:
            await _handle_provider_error(response_data, metadata, event_emitter, save_to_chat, adapters)
            if 'selected_model_id' in response_data and save_to_chat:
                await adapters.upsert_message(
                    metadata['chat_id'],
                    metadata['message_id'],
                    {'selectedModelId': response_data['selected_model_id']},
                    touch=False,
                )

            choices = response_data.get('choices', [])
            response_output = response_data.get('output')
            content = (choices[0].get('message', {}).get('content') or '') if choices else ''
            await _complete_emitted_turn(
                ctx,
                response_data,
                choices,
                content,
                response_output,
                continuing,
                save_to_chat,
                adapters,
            )
            return build_response_object(response, merge_events_into_response(response_data, events))
        except Exception as e:
            log.debug('Error occurred while processing request: %s', e)
            await adapters.publish_failed_event(request, user, metadata, str(e))
            return response

    return await _handle_response_without_event_emitter(response, response_data, ctx, adapters)
