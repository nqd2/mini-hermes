"""Resolve request tools and prepare the provider-facing native or legacy tool flow."""

import hashlib
import logging
from dataclasses import dataclass
from html import escape
from typing import Any

from fastapi import HTTPException

from open_webui.utils.json_codec import JSONCodec


@dataclass
class ToolResolutionAdapters:
    config_get: Any
    connect_mcp_server: Any
    get_tools: Any
    get_terminal_tools: Any
    get_terminal_agents_md: Any
    add_terminal_agents_md: Any
    add_or_update_system_message: Any
    get_builtin_tool_spec: Any
    display_file: Any
    get_builtin_tools: Any
    get_attached_knowledge: Any
    filter_user_terminal_tools: Any
    apply_tool_search: Any
    legacy_tool_calling_handler: Any
    logger: logging.Logger


@dataclass
class ToolResolutionResult:
    form_data: dict
    metadata: dict
    sources: list
    tools_dict: dict


async def resolve_chat_tools(
    request,
    form_data,
    user,
    metadata,
    model,
    extra_params,
    sources,
    *,
    models,
    task_model_id,
    payload_tools,
    inlet_filter_tools,
    use_builtin_tools,
    model_builtin_tools,
    is_note_chat,
    features,
    resolved_skill_ids,
    adapters: ToolResolutionAdapters,
) -> ToolResolutionResult:
    """Resolve server/client tools, apply policy, then prepare provider tool schemas."""
    if payload_tools is not None:
        return ToolResolutionResult(form_data, metadata, sources, {})

    tools_dict, mcp_clients = await _resolve_selected_tools(
        request, form_data, user, metadata, extra_params, models, task_model_id, adapters
    )
    terminal_capability = await _resolve_terminal_tools(
        request, form_data, user, metadata, extra_params, model, tools_dict, adapters
    )
    _add_direct_server_tools(form_data, metadata, tools_dict, terminal_capability, adapters)
    await _add_terminal_agent_instructions(
        request, user, metadata, extra_params, form_data, terminal_capability, adapters
    )
    if mcp_clients:
        metadata['mcp_clients'] = mcp_clients

    await _inject_builtin_tools(
        request,
        form_data,
        user,
        metadata,
        model,
        extra_params,
        tools_dict,
        use_builtin_tools,
        model_builtin_tools,
        is_note_chat,
        features,
        resolved_skill_ids,
        adapters,
    )
    await adapters.filter_user_terminal_tools(request, user, tools_dict, metadata, extra_params)
    if tools_dict:
        metadata['tools'] = tools_dict
        form_data, sources = await _prepare_provider_tool_calls(
            request,
            form_data,
            extra_params,
            user,
            models,
            metadata,
            tools_dict,
            inlet_filter_tools,
            sources,
            adapters,
        )

    return ToolResolutionResult(form_data, metadata, sources, tools_dict)


async def _resolve_selected_tools(request, form_data, user, metadata, extra_params, models, task_model_id, adapters):
    tool_ids = metadata.get('tool_ids', None)
    adapters.logger.debug('tool_ids=%r', tool_ids)
    direct_tool_servers = metadata.get('tool_servers', None)
    adapters.logger.debug('direct_tool_servers=%r', direct_tool_servers)

    tools_dict = {}
    mcp_clients = {}
    mcp_tools_dict = {}
    if not tool_ids:
        return tools_dict, mcp_clients

    resolved_tool_ids = []
    for tool_id in tool_ids:
        if tool_id.startswith('server:mcp:'):
            await _connect_mcp_tool_server(
                request, form_data, user, extra_params, tool_id, mcp_clients, mcp_tools_dict, adapters
            )
        else:
            resolved_tool_ids.append(tool_id)

    if resolved_tool_ids:
        tools_dict = await adapters.get_tools(
            request,
            resolved_tool_ids,
            user,
            {
                **extra_params,
                '__model__': models[task_model_id],
                '__messages__': form_data['messages'],
                '__files__': metadata.get('files') or [],
            },
        )
    if mcp_tools_dict:
        tools_dict = {**tools_dict, **mcp_tools_dict}
    return tools_dict, mcp_clients


async def _connect_mcp_tool_server(request, form_data, user, extra_params, tool_id, clients, tools, adapters):
    server_id = tool_id[len('server:mcp:') :]
    try:
        result = await adapters.connect_mcp_server(request, server_id, user, extra_params)
        if result is None:
            return

        client, tool_specs = result
        clients[server_id] = client
        if client.instructions:
            form_data['messages'] = adapters.add_or_update_system_message(
                f'MCP server {JSONCodec.dumps(server_id)} instructions:\n{client.instructions}',
                form_data['messages'],
                append=True,
            )
        for tool_spec in tool_specs:

            async def make_tool_function(client, function_name):
                async def tool_function(**kwargs):
                    return await client.call_tool(function_name, function_args=kwargs)

                return tool_function

            tool_function = await make_tool_function(client, tool_spec['name'])
            tool_name = f'{server_id}_{tool_spec["name"]}'
            if len(tool_name) > 64:
                tool_name = f'{tool_name[:55]}_{hashlib.sha256(tool_name.encode()).hexdigest()[:8]}'
            tools[tool_name] = {
                'spec': {**tool_spec, 'name': tool_name},
                'callable': tool_function,
                'type': 'mcp',
                'client': client,
                'direct': False,
            }
    except Exception:
        adapters.logger.exception("Failed to connect to MCP server '%s'", server_id)
        event_emitter = extra_params.get('__event_emitter__')
        if event_emitter:
            await event_emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': 'tool_connection',
                        'description': f"Failed to connect to MCP server '{server_id}'",
                        'error': True,
                        'done': True,
                    },
                }
            )


async def _resolve_terminal_tools(request, form_data, user, metadata, extra_params, model, tools_dict, adapters):
    terminal_capability = (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('terminal', True)
    terminal_connections = await adapters.config_get('terminal_server.connections', []) or []
    terminal_connection_ids = {connection.get('id') for connection in terminal_connections}
    terminal_id = metadata.get('terminal_id')
    if terminal_id and terminal_capability and terminal_id in terminal_connection_ids:
        try:
            terminal_result = await adapters.get_terminal_tools(request, terminal_id, user, extra_params)
            if isinstance(terminal_result, tuple):
                terminal_tools, terminal_system_prompt = terminal_result
            else:
                terminal_tools = terminal_result
                terminal_system_prompt = None
            if terminal_tools:
                tools_dict.update(terminal_tools)
            if terminal_system_prompt:
                form_data['messages'] = adapters.add_or_update_system_message(
                    terminal_system_prompt, form_data['messages'], append=True
                )
        except Exception as error:
            adapters.logger.exception(error)
            raise HTTPException(status_code=503, detail=f'Terminal unavailable: {error}') from error
    return terminal_capability


def _add_direct_server_tools(form_data, metadata, tools_dict, terminal_capability, adapters):
    direct_tool_servers = metadata.get('tool_servers', None)
    if not direct_tool_servers:
        return
    for tool_server in direct_tool_servers:
        if tool_server.get('is_terminal') is True and not terminal_capability:
            continue
        tool_server = dict(tool_server)
        tool_server_system_prompt = tool_server.pop('system_prompt', None)
        if tool_server_system_prompt:
            form_data['messages'] = adapters.add_or_update_system_message(
                tool_server_system_prompt, form_data['messages'], append=True
            )
        tool_specs = tool_server.pop('specs', [])
        for tool in tool_specs:
            tools_dict[tool['name']] = {
                'spec': (
                    adapters.get_builtin_tool_spec(adapters.display_file)
                    if tool_server.get('is_terminal') and tool['name'] == 'display_file'
                    else tool
                ),
                'direct': True,
                'server': tool_server,
            }


async def _add_terminal_agent_instructions(
    request, user, metadata, extra_params, form_data, terminal_capability, adapters
):
    if metadata.get('terminal_id') and terminal_capability:
        agents_md = await adapters.get_terminal_agents_md(request, user, metadata, extra_params)
        if agents_md:
            form_data['messages'] = adapters.add_terminal_agents_md(form_data['messages'], agents_md)


async def _inject_builtin_tools(
    request,
    form_data,
    user,
    metadata,
    model,
    extra_params,
    tools_dict,
    use_builtin_tools,
    model_builtin_tools,
    is_note_chat,
    features,
    resolved_skill_ids,
    adapters,
):
    if not use_builtin_tools:
        return
    if model_builtin_tools.get('knowledge', True):
        _append_knowledge_context(form_data, model, metadata, adapters)

    builtin_tools = await adapters.get_builtin_tools(
        request,
        {
            **extra_params,
            '__event_emitter__': extra_params.get('__event_emitter__'),
            '__skill_ids__': sorted(resolved_skill_ids),
        },
        features,
        model,
        is_note_chat=is_note_chat,
    )
    _merge_builtin_tools(tools_dict, builtin_tools)


def _append_knowledge_context(form_data, model, metadata, adapters):
    knowledge_tags = []
    for item in adapters.get_attached_knowledge(model, metadata):
        if not item.get('id') or not item.get('type'):
            continue
        attrs = f'type="{escape(str(item["type"]), quote=True)}" id="{escape(str(item["id"]), quote=True)}"'
        if item.get('name'):
            attrs += f' name="{escape(str(item["name"]), quote=True)}"'
        if item.get('source'):
            attrs += f' source="{escape(str(item["source"]), quote=True)}"'
        knowledge_tags.append(f'<knowledge {attrs}/>')
    if knowledge_tags:
        form_data['messages'] = adapters.add_or_update_system_message(
            '<attached_knowledge>\n' + '\n'.join(knowledge_tags) + '\n</attached_knowledge>',
            form_data['messages'],
            append=True,
        )


def _merge_builtin_tools(tools_dict, builtin_tools):
    for name, tool_dict in builtin_tools.items():
        if name not in tools_dict:
            tools_dict[name] = tool_dict
        elif name == 'display_file' and (
            tools_dict[name].get('type') == 'terminal' or tools_dict[name].get('server', {}).get('is_terminal') is True
        ):
            tools_dict[name] = {**tools_dict[name], 'file_tool': tool_dict}


async def _prepare_provider_tool_calls(
    request,
    form_data,
    extra_params,
    user,
    models,
    metadata,
    tools_dict,
    inlet_filter_tools,
    sources,
    adapters,
):
    if metadata.get('params', {}).get('function_calling') != 'legacy':
        deferred = await adapters.apply_tool_search(form_data, metadata, tools_dict, inlet_filter_tools)
        form_data['tools'] = [
            {'type': 'function', 'function': tool.get('spec', {})}
            for name, tool in tools_dict.items()
            if name not in deferred
        ]
        if inlet_filter_tools:
            form_data['tools'].extend(inlet_filter_tools)
        return form_data, sources

    try:
        form_data, flags = await adapters.legacy_tool_calling_handler(
            request, form_data, extra_params, user, models, tools_dict
        )
        sources.extend(flags.get('sources', []))
    except Exception as error:
        adapters.logger.exception(error)
    return form_data, sources
