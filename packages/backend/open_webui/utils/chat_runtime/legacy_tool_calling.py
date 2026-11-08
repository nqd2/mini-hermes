"""Legacy text-completion tool-call planning and execution workflow."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from open_webui.config import DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
from open_webui.constants import TASKS
from open_webui.models.config import Config
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.chat_runtime.tool_results import process_tool_result
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import get_content_from_message, get_last_user_message
from open_webui.utils.task import get_task_model_id, tools_function_calling_generation_template

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LegacyToolCallingAdapters:
    """Middleware callbacks needed to execute client-side tool effects."""

    terminal_event_handler: Callable[..., Awaitable[None]]
    store_tool_result_image: Callable[..., Awaitable[str]]


async def _get_content_from_response(response) -> str | None:
    content = None
    if hasattr(response, 'body_iterator'):
        async for chunk in response.body_iterator:
            data = JSONCodec.loads(chunk.decode('utf-8', 'replace'))
            content = data['choices'][0]['message']['content']

        if response.background is not None:
            await response.background()
    else:
        content = response['choices'][0]['message']['content']
    return content


def _build_function_calling_payload(messages, task_model_id, content):
    user_message = get_last_user_message(messages)
    if user_message and messages and messages[-1]['role'] == 'user':
        messages = messages[:-1]

    recent_messages = messages[-4:] if len(messages) > 4 else messages
    chat_history = '\n'.join(
        f'{message["role"].upper()}: """{get_content_from_message(message)}"""' for message in recent_messages
    )
    prompt = f'History:\n{chat_history}\nQuery: {user_message}' if chat_history else f'Query: {user_message}'
    return {
        'model': task_model_id,
        'messages': [
            {'role': 'system', 'content': content},
            {'role': 'user', 'content': prompt},
        ],
        'stream': False,
        'metadata': {'task': str(TASKS.FUNCTION_CALLING)},
    }


async def _invoke_tool(request, metadata, user, tool_name, tool_call, tools, event_caller):
    tool_function_params = tool_call.get('parameters', {})
    tool_type = ''
    direct_tool = False
    try:
        tool = tools[tool_name]
        tool_type = tool.get('type', '')
        direct_tool = tool.get('direct', False)
        if tool_type != 'mcp':
            allowed_params = tool.get('spec', {}).get('parameters', {}).get('properties', {}).keys()
            tool_function_params = {key: value for key, value in tool_function_params.items() if key in allowed_params}

        if direct_tool:
            tool_result = await event_caller(
                {
                    'type': 'execute:tool',
                    'data': {
                        'id': str(uuid4()),
                        'name': tool_name,
                        'params': tool_function_params,
                        'server': tool.get('server', {}),
                        'session_id': metadata.get('session_id'),
                    },
                }
            )
        else:
            tool_result = await tool['callable'](**tool_function_params)
    except Exception as e:
        tool_result = {'error': str(e)}

    tool_result, files, embeds = await process_tool_result(
        request,
        tool_name,
        tool_result,
        tool_type,
        direct_tool,
        metadata,
        user,
    )
    return tool_result, files, embeds, tool_function_params


async def _emit_tool_effects(
    request, metadata, user, tool_name, params, result, files, embeds, event_emitter, adapters
):
    if not event_emitter:
        return
    await adapters.terminal_event_handler(tool_name, params, result, event_emitter)
    if files:
        for file_item in files:
            if file_item.get('type') == 'image':
                file_item['url'] = await adapters.store_tool_result_image(
                    request,
                    file_item.get('url'),
                    metadata,
                    user,
                )
        await event_emitter({'type': 'files', 'data': {'files': files}})
    if embeds:
        await event_emitter({'type': 'embeds', 'data': {'embeds': embeds}})


async def _handle_tool_call(request, metadata, user, tool_call, tools, event_caller, event_emitter, adapters):
    tool_name = tool_call.get('name')
    log.debug('Tool-call candidate received: name=%s', tool_name)
    if tool_name not in tools:
        log.warning('Tool "%s" not found', tool_name)
        return None, False

    result, files, embeds, params = await _invoke_tool(
        request,
        metadata,
        user,
        tool_name,
        tool_call,
        tools,
        event_caller,
    )
    await _emit_tool_effects(request, metadata, user, tool_name, params, result, files, embeds, event_emitter, adapters)
    if not result:
        return None, False

    tool = tools[tool_name]
    tool_id = tool.get('tool_id', '')
    source_name = f'{tool_id}/{tool_name}' if tool_id else tool_name
    source = {
        'source': {'name': source_name},
        'document': [str(result)],
        'metadata': [{'source': source_name, 'parameters': params}],
        'tool_result': True,
    }
    skip_files = tool.get('metadata', {}).get('file_handler', False)
    return source, skip_files


async def _handle_tool_calls(
    request,
    metadata,
    user,
    result,
    tools,
    event_caller,
    event_emitter,
    adapters,
    sources,
    state,
):
    tool_calls = result.get('tool_calls')
    if not tool_calls:
        tool_calls = [result]
    for tool_call in tool_calls:
        source, handles_files = await _handle_tool_call(
            request,
            metadata,
            user,
            tool_call,
            tools,
            event_caller,
            event_emitter,
            adapters,
        )
        if source:
            sources.append(source)
        state['skip_files'] = state['skip_files'] or handles_files


async def run_legacy_tool_calling(
    request,
    body,
    extra_params,
    user,
    models,
    tools,
    adapters: LegacyToolCallingAdapters,
):
    event_caller = extra_params['__event_call__']
    event_emitter = extra_params['__event_emitter__']
    metadata = extra_params['__metadata__']
    task_config = await Config.get_many(
        'task.model.default',
        'task.model.external',
        'task.tools.prompt_template',
    )
    task_model_id = get_task_model_id(
        body['model'],
        task_config.get('task.model.default'),
        task_config.get('task.model.external'),
        models,
    )
    specs = [tool['spec'] for tool in tools.values()]
    tools_specs = JSONCodec.dumps(specs, ensure_ascii=False)
    tools_prompt_template = task_config.get('task.tools.prompt_template')
    template = tools_prompt_template if tools_prompt_template != '' else DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    tools_function_calling_prompt = tools_function_calling_generation_template(template, tools_specs)
    payload = _build_function_calling_payload(body['messages'], task_model_id, tools_function_calling_prompt)
    sources = []
    state = {'skip_files': False}
    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        content = await _get_content_from_response(response)
        if not content:
            return body, {}
        content = content[content.find('{') : content.rfind('}') + 1]
        if not content:
            raise ValueError('No JSON object found in the response')
        result = JSONCodec.loads(content)
        await _handle_tool_calls(
            request,
            metadata,
            user,
            result,
            tools,
            event_caller,
            event_emitter,
            adapters,
            sources,
            state,
        )
    except Exception as e:
        log.debug('Error: %s', e)

    log.debug('tool_contexts: %s', sources)
    if state['skip_files'] and 'files' in body.get('metadata', {}):
        del body['metadata']['files']
    return body, {'sources': sources}
