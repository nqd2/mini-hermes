import pytest


def test_chat_middleware_has_a_module_logger_for_error_paths():
    import logging

    from open_webui.utils import middleware

    assert isinstance(middleware.log, logging.Logger)


@pytest.mark.asyncio
async def test_execute_tool_call_filters_arguments_and_keeps_legacy_import():
    from open_webui.utils import middleware
    from open_webui.utils.chat_runtime.tool_execution import execute_tool_call

    assert middleware.execute_tool_call is execute_tool_call

    observed = {}

    async def inspect_tool(value):
        observed['value'] = value
        return {'value': value}

    tool_call = {
        'name': 'inspect',
        'arguments': '{"value": 42, "ignored": "drop me"}',
    }
    metadata = {
        'chat_id': 'temporary-chat',
        'params': {},
        'tools': {
            'inspect': {
                'callable': inspect_tool,
                'spec': {'parameters': {'properties': {'value': {'type': 'integer'}}}},
                'type': 'builtin',
            },
        },
    }

    params, result, tool, tool_type, direct = await execute_tool_call(
        {},
        metadata,
        None,
        tool_call,
    )

    assert params == {'value': 42}
    assert observed == {'value': 42}
    assert result == {'value': 42}
    assert tool_type == 'builtin'
    assert direct is False
    assert tool['callable'] is inspect_tool
    assert tool_call['arguments'] == '{"value":42,"ignored":"drop me"}'


@pytest.mark.asyncio
async def test_independent_greeting_blocks_file_read_before_tool_execution():
    from open_webui.utils.chat_runtime.tool_execution import execute_tool_call

    calls = []

    async def read_file():
        calls.append('read')
        return {'content': 'private file contents'}

    metadata = {
        'chat_id': 'temporary-chat',
        'params': {},
        'resource_aware_files': True,
        'resource_greeting': True,
        'tools': {
            'view_file': {
                'callable': read_file,
                'spec': {'parameters': {'properties': {}}},
                'type': 'builtin',
            },
        },
    }

    params, result, *_ = await execute_tool_call(
        {},
        metadata,
        None,
        {'name': 'view_file', 'arguments': '{}'},
    )

    assert params == {}
    assert result['code'] == 'resource_access_not_requested'
    assert result['status'] == 'blocked'
    assert calls == []


@pytest.mark.asyncio
async def test_malformed_tool_arguments_return_a_tool_error_without_escaping():
    from open_webui.utils.chat_runtime.tool_execution import execute_tool_call

    params, result, tool, tool_type, direct = await execute_tool_call(
        {},
        {'tools': {'inspect': {'callable': None}}, 'chat_id': 'temporary-chat', 'params': {}},
        None,
        {'name': 'inspect', 'arguments': '{not valid json'},
    )

    assert params == {}
    assert result == 'Error: Tool arguments are not valid JSON.'
    assert tool is None
    assert tool_type is None
    assert direct is False


def test_bare_upload_uuid_routes_display_file_to_the_builtin_file_tool():
    from open_webui.utils.chat_runtime.tool_execution import _resolve_tool_target

    terminal = {
        'type': 'terminal',
        'direct': True,
        'server': {'is_terminal': True},
        'spec': {'parameters': {'properties': {'path': {'type': 'string'}}}},
    }
    params = {'path': '123e4567-e89b-12d3-a456-426614174000'}

    resolved_params, resolved_tool, tool_type, direct, is_file_tool = _resolve_tool_target(
        'display_file', params, terminal
    )

    assert resolved_params == params
    assert resolved_tool is terminal
    assert tool_type == 'terminal'
    assert direct is True
    assert is_file_tool is True
