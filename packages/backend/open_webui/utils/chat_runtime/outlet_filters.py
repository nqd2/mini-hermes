"""Post-response outlet filter execution and persistence."""

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.chat_runtime.stream_state import output_id
from open_webui.utils.misc import get_message_list, get_output_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutletFilterAdapters:
    """Open WebUI callbacks needed by the post-response filter workflow."""

    functions_enabled: bool
    get_filter_functions: Callable[..., Awaitable[list]]
    get_sorted_filters: Callable[..., list]
    process_pipeline_outlet_filter: Callable[..., Awaitable[dict]]
    process_filter_functions: Callable[..., Awaitable[tuple[dict, Any]]]
    get_models: Callable[[Any], Any]
    get_messages_map_by_chat_id: Callable[[str], Awaitable[dict | None]]
    upsert_message: Callable[..., Awaitable[Any]]
    user_payload: Callable[[Any], dict]


async def _load_filter_messages(ctx, chat_id, message_id, is_unsaved_chat, adapters):
    if is_unsaved_chat:
        form_messages = ctx.get('form_data', {}).get('messages', [])
        assistant_message = ctx.get('assistant_message', {})
        message_list = [
            {
                'role': message.get('role'),
                'content': message.get('content') or get_output_text(message.get('output')),
            }
            for message in form_messages
        ]
        if assistant_message:
            message_list.append({'id': message_id, 'role': 'assistant', **assistant_message})
        return None, message_list

    messages_map = await adapters.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None, None
    return messages_map, get_message_list(messages_map, message_id)


def _build_outlet_data(ctx, message_list, model_id, chat_id, message_id):
    metadata = ctx['metadata']
    finish_reason = (ctx.get('assistant_message') or {}).get('finish_reason')
    return {
        'model': model_id,
        'messages': [
            {
                'id': message.get('id'),
                'role': message.get('role'),
                'content': message.get('content') or get_output_text(message.get('output')),
                'info': message.get('info'),
                'timestamp': message.get('timestamp'),
                # Deepcopy so in-place filter mutations do not alias messages_map's baseline
                **({'output': copy.deepcopy(message['output'])} if message.get('output') else {}),
                **({'usage': message['usage']} if message.get('usage') else {}),
                **({'sources': message['sources']} if message.get('sources') else {}),
                **({'finish_reason': finish_reason} if finish_reason and message.get('id') == message_id else {}),
            }
            for message in message_list
        ],
        'filter_ids': metadata.get('filter_ids', []),
        'chat_id': chat_id,
        'session_id': metadata.get('session_id'),
        'id': message_id,
    }


async def _persist_outlet_changes(chat_id, messages_map, messages, adapters):
    if not messages_map:
        return

    for message in messages:
        message_id = message.get('id')
        if not message_id or message_id not in messages_map:
            continue

        original_message = messages_map[message_id]
        original_content = original_message.get('content') or get_output_text(original_message.get('output'))
        message_content = message.get('content') or get_output_text(message.get('output'))
        content_changed = original_content != message_content
        output_changed = message.get('output') and message.get('output') != original_message.get('output')
        if not content_changed and not output_changed:
            continue

        message_update = {
            'originalContent': original_content,
            **({'output': message['output']} if output_changed else {}),
        }
        if content_changed:
            message_update['content'] = message_content or ''
        await adapters.upsert_message(chat_id, message_id, message_update)


async def run_outlet_filters(ctx, adapters: OutletFilterAdapters):
    """Run outlet filters inline after chat completion.

    Replaces the separate POST /api/chat/completed round-trip.
    Persists outlet-modified content to DB and emits a chat:outlet event
    so the frontend can sync its in-memory state. Returns immediately when
    the model has no filters.

    For temp/API chats, messages are built from form_data plus ctx['assistant_message'].
    """
    request = ctx['request']
    user = ctx['user']
    model = ctx['model']
    metadata = ctx['metadata']
    event_emitter = ctx.get('event_emitter')
    event_caller = ctx.get('event_caller')
    chat_id = metadata.get('chat_id', '')
    message_id = metadata.get('message_id')

    if not chat_id and not ctx.get('assistant_message'):
        return
    if not message_id:
        message_id = output_id('msg')

    is_unsaved_chat = not is_saved_chat_id(chat_id)
    try:
        filter_functions = (
            await adapters.get_filter_functions(request, model, metadata.get('filter_ids', []))
            if adapters.functions_enabled
            else []
        )
        model_id = model.get('id') if isinstance(model, dict) else model
        models = adapters.get_models(request)
        has_pipeline_outlet_filters = bool(
            (isinstance(model, dict) and 'pipeline' in model) or adapters.get_sorted_filters(model_id, models)
        )
        if not filter_functions and not has_pipeline_outlet_filters:
            return

        messages_map, message_list = await _load_filter_messages(ctx, chat_id, message_id, is_unsaved_chat, adapters)
        if not message_list:
            return

        outlet_data = _build_outlet_data(ctx, message_list, model_id, chat_id, message_id)
        try:
            outlet_data = await adapters.process_pipeline_outlet_filter(request, outlet_data, user, models)
        except Exception as e:
            log.debug('Pipeline outlet filter error: %s', e)

        extra_params = {
            '__event_emitter__': event_emitter,
            '__event_call__': event_caller,
            '__user__': adapters.user_payload(user),
            '__metadata__': metadata,
            '__request__': request,
            '__model__': model,
        }
        if filter_functions:
            outlet_result, _ = await adapters.process_filter_functions(
                request=request,
                filter_context=None,
                filter_functions=filter_functions,
                filter_type='outlet',
                form_data=outlet_data,
                extra_params=extra_params,
            )
        else:
            outlet_result = outlet_data

        if not outlet_result or not outlet_result.get('messages'):
            return

        if not is_unsaved_chat:
            await _persist_outlet_changes(chat_id, messages_map, outlet_result['messages'], adapters)
        if event_emitter:
            await event_emitter({'type': 'chat:outlet', 'data': {'messages': outlet_result['messages']}})
    except Exception as e:
        log.debug('Error running outlet filters: %s', e)
