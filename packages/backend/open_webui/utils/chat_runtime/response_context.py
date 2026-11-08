"""Build request-scoped context shared by chat response handlers."""

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate


@dataclass(frozen=True)
class ResponseContextAdapters:
    """Application callbacks needed to prepare response state."""

    get_event_emitter: Callable[[dict], Awaitable[Any]]
    get_event_call: Callable[[dict], Awaitable[Any]]
    get_saved_message: Callable[[str, str], Awaitable[dict | None]]
    is_saved_chat_id: Callable[[str | None], bool] = is_saved_chat_id


async def resolve_response_event_handlers(metadata: dict, adapters: ResponseContextAdapters) -> tuple[Any, Any]:
    event_emitter = None
    event_caller = None
    if metadata.get('chat_id') and metadata.get('message_id'):
        event_emitter = await adapters.get_event_emitter(metadata)
    if metadata.get('session_id') and metadata.get('chat_id') and metadata.get('message_id'):
        event_caller = await adapters.get_event_call(metadata)
    return event_emitter, event_caller


async def build_response_context(
    request: Any,
    form_data: dict,
    user: Any,
    model: dict,
    metadata: dict,
    tasks: Any,
    events: list,
    adapters: ResponseContextAdapters,
) -> dict:
    event_emitter, event_caller = await resolve_response_event_handlers(metadata, adapters)
    answer_gate = ResourceAnswerGate(metadata)
    event_emitter = answer_gate.bind(event_emitter)
    assistant_message = None
    if metadata.get('assistant_message_id'):
        # Preserve the original output before request conversion strips it from temporary chats.
        if adapters.is_saved_chat_id(metadata.get('chat_id')):
            assistant_message = await adapters.get_saved_message(metadata['chat_id'], metadata['assistant_message_id'])
        elif form_data.get('messages') and form_data['messages'][-1].get('role') == 'assistant':
            assistant_message = form_data['messages'][-1]
    return {
        'request': request,
        'form_data': form_data,
        'user': user,
        'model': model,
        'metadata': metadata,
        'tasks': tasks,
        'events': events,
        'event_emitter': event_emitter,
        'event_caller': event_caller,
        'resource_answer_gate': answer_gate,
        'assistant_message': copy.deepcopy(assistant_message),
    }
