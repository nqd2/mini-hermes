"""Application workflow for resuming approved tool calls in a chat response."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from open_webui.env import ENABLE_FUNCTIONS
from open_webui.models.chats import Chats
from open_webui.models.users import UserModel
from open_webui.utils.chat_runtime.citations import CITATION_TOOL_NAMES, get_citation_source_from_tool_result
from open_webui.utils.chat_runtime.messages import (
    get_reasoning_format,
    normalize_messages_for_model,
    process_messages_with_output,
    strip_reasoning_details,
)
from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate, prepare_resource_tool_result
from open_webui.utils.chat_runtime.stream_state import output_id
from open_webui.utils.chat_runtime.tool_execution import execute_tool_call
from open_webui.utils.chat_runtime.tool_results import (
    _is_tool_result_error,
    build_terminal_file_tool_result,
    process_tool_result,
)
from open_webui.utils.filter import get_filter_context, get_filter_functions, process_filter_functions
from open_webui.utils.oauth import get_system_oauth_token
from open_webui.utils.tool_approval import complete_tool_call, pending_tool_calls


@dataclass(frozen=True)
class ToolResumptionAdapters:
    """Request-boundary callbacks supplied by the Open WebUI middleware adapter."""

    get_event_emitter_and_caller: Callable[[dict], Awaitable[tuple[Any, Any]]]
    convert_url_images_to_base64: Callable[..., Awaitable[dict]]
    terminal_event_handler: Callable[..., Awaitable[None]]
    store_tool_result_image: Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class ToolCallClaim:
    item: dict[str, Any] | None = None
    waiting: bool = False


async def _prepare_resume_form_data(
    request: Any,
    form_data: dict,
    user: Any,
    model: dict,
    metadata: dict,
    message: dict,
    event_emitter: Any,
    event_caller: Any,
    adapters: ToolResumptionAdapters,
) -> dict:
    assistant_message = dict(message)
    if assistant_message.get('model') != model['id']:
        assistant_message['output'] = strip_reasoning_details(message.get('output') or [])
    form_data = {
        **form_data,
        'messages': copy.deepcopy(
            [
                *form_data['messages'],
                *process_messages_with_output([assistant_message], reasoning_format=get_reasoning_format(model)),
            ]
        ),
    }
    await adapters.convert_url_images_to_base64(form_data, user=user)
    if ENABLE_FUNCTIONS:
        filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))
        if filter_functions:
            form_data, _ = await process_filter_functions(
                request=request,
                filter_context=get_filter_context(request),
                filter_functions=filter_functions,
                filter_type='request',
                form_data=form_data,
                extra_params={
                    '__event_emitter__': event_emitter,
                    '__event_call__': event_caller,
                    '__user__': user.model_dump() if isinstance(user, UserModel) else {},
                    '__metadata__': metadata,
                    '__oauth_token__': await get_system_oauth_token(request, user),
                    '__request__': request,
                    '__model__': model,
                    '__chat_id__': metadata.get('chat_id'),
                    '__message_id__': metadata.get('message_id'),
                },
            )
    normalize_messages_for_model(form_data)
    return form_data


async def _claim_next_tool_call(chat_id: str, message_id: str, metadata: dict) -> ToolCallClaim:
    async with Chats.edit_message_output(chat_id, message_id) as saved:
        if not saved:
            raise HTTPException(status_code=404, detail='Message not found.')
        output = saved.get('output') or []
        pending = pending_tool_calls(output)
        if any(entry.get('status') == 'in_progress' for entry in pending):
            return ToolCallClaim(waiting=True)

        item = next(
            (
                entry
                for entry in pending
                if entry.get('name') != 'ask_user'
                and (entry.get('approved') is True or metadata.get('params', {}).get('tool_approval_mode') == 'full')
            ),
            None,
        )
        if item is None:
            if pending:
                pending[0]['status'] = 'pending'
            # A stale continuation must not start another model response.
            return ToolCallClaim(waiting=True)

        item['call_id'] = item.get('call_id') or item['id']
        item['status'] = 'in_progress'
        saved['done'] = False
        return ToolCallClaim(item=copy.deepcopy(item))


async def _execute_and_normalize_tool_result(
    request: Any,
    form_data: dict,
    metadata: dict,
    user: Any,
    event_caller: Any,
    event_emitter: Any,
    item: dict,
    adapters: ToolResumptionAdapters,
) -> tuple[dict, Any, dict | None, list, list]:
    params, result, tool, tool_type, direct_tool = await execute_tool_call(form_data, metadata, event_caller, item)
    files, embeds = [], []
    if result is None and tool is None:
        result = 'Error: Tool call arguments could not be parsed. The model generated malformed or incomplete JSON.'
    elif tool:
        name = item.get('name', '')
        terminal_file_result = build_terminal_file_tool_result(name, params, result, tool, metadata)
        if terminal_file_result:
            result = terminal_file_result
        result, files, embeds = await process_tool_result(request, name, result, tool_type, direct_tool, metadata, user)
        await adapters.terminal_event_handler(name, params, result, event_emitter)
    return params, result, tool, files, embeds


async def _build_function_call_output(
    request: Any,
    model: dict,
    metadata: dict,
    user: Any,
    event_emitter: Any,
    item: dict,
    params: dict,
    result: Any,
    tool: dict | None,
    files: list,
    embeds: list,
    adapters: ToolResumptionAdapters,
) -> dict:
    name = item.get('name', '')
    content, result_supplied = prepare_resource_tool_result(metadata, name, result)
    citations_enabled = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('citations', True)
    if tool and event_emitter and citations_enabled and name in CITATION_TOOL_NAMES and result and result_supplied:
        for source in get_citation_source_from_tool_result(
            tool_name=name, tool_params=params, tool_result=result, tool_id=tool.get('tool_id', '')
        ):
            await event_emitter({'type': 'source', 'data': source})

    output_parts = [{'type': 'input_text', 'text': content}]
    item['status'] = 'failed' if _is_tool_result_error(content) else 'completed'
    display_files = []
    for file_item in files:
        if file_item.get('type') == 'image' and file_item.get('url', '').startswith('data:'):
            image_url = await adapters.store_tool_result_image(request, file_item['url'], metadata, user)
            output_parts.append({'type': 'input_image', 'image_url': image_url})
        else:
            display_files.append(file_item)
            if file_item.get('type') == 'image' and file_item.get('url'):
                output_parts.append({'type': 'input_image', 'image_url': file_item['url']})

    return {
        'type': 'function_call_output',
        'id': output_id('fco'),
        'call_id': item['call_id'],
        'output': output_parts,
        'status': item['status'],
        **({'files': display_files} if display_files else {}),
        **({'embeds': embeds} if embeds else {}),
    }


async def _complete_claimed_tool_call(
    chat_id: str,
    message_id: str,
    request: Any,
    form_data: dict,
    user: Any,
    model: dict,
    metadata: dict,
    event_emitter: Any,
    event_caller: Any,
    answer_gate: ResourceAnswerGate,
    item: dict,
    adapters: ToolResumptionAdapters,
) -> list[dict]:
    try:
        params, result, tool, files, embeds = await _execute_and_normalize_tool_result(
            request, form_data, metadata, user, event_caller, event_emitter, item, adapters
        )
        result_item = await _build_function_call_output(
            request, model, metadata, user, event_emitter, item, params, result, tool, files, embeds, adapters
        )
        return await complete_tool_call(
            chat_id,
            message_id,
            item,
            result_item,
            output_filter=answer_gate.present,
        )
    except BaseException:
        item['status'] = 'incomplete'
        await asyncio.shield(
            complete_tool_call(
                chat_id,
                message_id,
                item,
                {
                    'type': 'function_call_output',
                    'id': output_id('fco'),
                    'call_id': item['call_id'],
                    'status': 'incomplete',
                    'output': [
                        {
                            'type': 'input_text',
                            'text': 'Error: tool execution was interrupted; its outcome is unknown.',
                        }
                    ],
                },
                output_filter=answer_gate.present,
            )
        )
        raise


async def resume_tool_calls(
    request: Any,
    form_data: dict,
    user: Any,
    model: dict,
    metadata: dict,
    message: dict,
    *,
    adapters: ToolResumptionAdapters,
) -> bool:
    """Execute approved calls while preserving the saved output and continuation contract."""
    chat_id = metadata.get('chat_id')
    message_id = metadata.get('message_id') or metadata.get('assistant_message_id')
    if not isinstance(message.get('output'), list):
        return False

    event_emitter, event_caller = await adapters.get_event_emitter_and_caller(metadata)
    answer_gate = ResourceAnswerGate(metadata)
    form_data = await _prepare_resume_form_data(
        request, form_data, user, model, metadata, message, event_emitter, event_caller, adapters
    )

    while True:
        claim = await _claim_next_tool_call(chat_id, message_id, metadata)
        if claim.waiting:
            return True
        item = claim.item
        output = await _complete_claimed_tool_call(
            chat_id,
            message_id,
            request,
            form_data,
            user,
            model,
            metadata,
            event_emitter,
            event_caller,
            answer_gate,
            item,
            adapters,
        )
        if event_emitter:
            await event_emitter({'type': 'chat:completion', 'data': {'done': False, 'output': output}})
        pending = pending_tool_calls(output)
        if not pending:
            message['output'] = output
            return False
        if metadata.get('params', {}).get('tool_approval_mode') != 'full' and not any(
            entry.get('approved') is True for entry in pending
        ):
            return True
