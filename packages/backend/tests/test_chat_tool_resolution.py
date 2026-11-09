from unittest.mock import AsyncMock, Mock

import pytest

from open_webui.utils.chat_runtime.tool_resolution import ToolResolutionAdapters, resolve_chat_tools
from open_webui.utils.misc import add_or_update_system_message


def _adapters(**overrides):
    async def unused(*_args, **_kwargs):
        pytest.fail('unexpected tool-resolution adapter call')

    values = {
        'config_get': AsyncMock(return_value=[]),
        'connect_mcp_server': AsyncMock(return_value=None),
        'get_tools': AsyncMock(return_value={}),
        'get_terminal_tools': AsyncMock(return_value={}),
        'get_terminal_agents_md': AsyncMock(return_value=None),
        'add_terminal_agents_md': lambda messages, content: messages + [{'role': 'user', 'content': content}],
        'add_or_update_system_message': add_or_update_system_message,
        'get_builtin_tool_spec': lambda tool: tool,
        'display_file': object(),
        'get_builtin_tools': AsyncMock(return_value={}),
        'get_attached_knowledge': lambda model, metadata: [],
        'filter_user_terminal_tools': AsyncMock(),
        'apply_tool_search': AsyncMock(return_value=[]),
        'legacy_tool_calling_handler': AsyncMock(),
        'logger': Mock(),
    }
    values.update(overrides)
    return ToolResolutionAdapters(**values)


def _arguments(*, request, form_data, user, metadata, model, extra_params, adapters, **overrides):
    values = {
        'request': request,
        'form_data': form_data,
        'user': user,
        'metadata': metadata,
        'model': model,
        'extra_params': extra_params,
        'sources': [],
        'models': {'task-model': {'id': 'task-model'}},
        'task_model_id': 'task-model',
        'payload_tools': None,
        'inlet_filter_tools': None,
        'use_builtin_tools': False,
        'model_builtin_tools': {},
        'is_note_chat': False,
        'features': {},
        'resolved_skill_ids': set(),
        'adapters': adapters,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_explicit_caller_tools_bypass_server_resolution_unchanged():
    form_data = {'messages': [{'role': 'user', 'content': 'hello'}], 'tools': []}
    metadata = {'tool_ids': ['server-tool'], 'params': {'function_calling': 'native'}}
    adapters = _adapters()

    result = await resolve_chat_tools(
        **_arguments(
            request=object(),
            form_data=form_data,
            user=object(),
            metadata=metadata,
            model={},
            extra_params={},
            adapters=adapters,
            payload_tools=[],
        )
    )

    assert result.form_data is form_data
    assert result.metadata is metadata
    assert result.form_data['tools'] == []
    adapters.get_tools.assert_not_awaited()
    adapters.get_builtin_tools.assert_not_awaited()
    adapters.config_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_resolution_merges_server_tools_and_inlet_tools_in_order():
    request = object()
    user = object()
    messages = [{'role': 'user', 'content': 'find a program'}]
    form_data = {'model': 'chat-model', 'messages': messages}
    metadata = {
        'tool_ids': ['catalog-tool'],
        'params': {'function_calling': 'native'},
        'files': [{'id': 'file-1'}],
    }
    tool = {'tool_id': 'catalog-tool', 'spec': {'name': 'lookup_program', 'parameters': {}}, 'callable': object()}
    inlet_tool = {'type': 'function', 'function': {'name': 'inlet-added'}}
    adapters = _adapters(
        get_tools=AsyncMock(return_value={'lookup_program': tool}),
    )

    result = await resolve_chat_tools(
        **_arguments(
            request=request,
            form_data=form_data,
            user=user,
            metadata=metadata,
            model={'id': 'chat-model'},
            extra_params={'trace': 'trace-1'},
            adapters=adapters,
            inlet_filter_tools=[inlet_tool],
        )
    )

    adapters.get_tools.assert_awaited_once_with(
        request,
        ['catalog-tool'],
        user,
        {
            'trace': 'trace-1',
            '__model__': {'id': 'task-model'},
            '__messages__': messages,
            '__files__': [{'id': 'file-1'}],
        },
    )
    adapters.apply_tool_search.assert_awaited_once_with(form_data, metadata, {'lookup_program': tool}, [inlet_tool])
    assert form_data['tools'] == [
        {'type': 'function', 'function': {'name': 'lookup_program', 'parameters': {}}},
        inlet_tool,
    ]
    assert metadata['tools'] == {'lookup_program': tool}
    assert result.tools_dict == {'lookup_program': tool}


@pytest.mark.asyncio
async def test_legacy_resolution_appends_tool_handler_sources_without_native_schema():
    request = object()
    user = object()
    form_data = {'model': 'chat-model', 'messages': [{'role': 'user', 'content': 'find a program'}]}
    metadata = {'tool_ids': ['catalog-tool'], 'params': {'function_calling': 'legacy'}}
    tool = {'tool_id': 'catalog-tool', 'spec': {'name': 'lookup_program'}, 'callable': object()}
    source = {'source': {'name': 'catalog-tool'}, 'document': ['matched result']}
    handler_form_data = {**form_data, 'legacy_plan': True}
    legacy_handler = AsyncMock(return_value=(handler_form_data, {'sources': [source]}))
    adapters = _adapters(
        get_tools=AsyncMock(return_value={'lookup_program': tool}),
        legacy_tool_calling_handler=legacy_handler,
    )

    result = await resolve_chat_tools(
        **_arguments(
            request=request,
            form_data=form_data,
            user=user,
            metadata=metadata,
            model={'id': 'chat-model'},
            extra_params={},
            adapters=adapters,
        )
    )

    legacy_handler.assert_awaited_once_with(
        request,
        form_data,
        {},
        user,
        {'task-model': {'id': 'task-model'}},
        {'lookup_program': tool},
    )
    adapters.apply_tool_search.assert_not_awaited()
    assert result.sources == [source]
    assert result.form_data is handler_form_data
    assert result.form_data['legacy_plan'] is True
    assert 'tools' not in form_data
