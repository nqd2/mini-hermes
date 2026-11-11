import sys
import types
from importlib import import_module
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_document_summary_tools_stay_loaded_when_tool_search_defers_large_schemas(monkeypatch):
    config_module = types.ModuleType('open_webui.models.config')
    config_module.Config = type('Config', (), {})
    monkeypatch.setitem(sys.modules, 'open_webui.models.config', config_module)

    misc_module = types.ModuleType('open_webui.utils.misc')
    misc_module.add_or_update_system_message = lambda _text, messages, **_kwargs: messages
    monkeypatch.setitem(sys.modules, 'open_webui.utils.misc', misc_module)

    tool_utils = types.ModuleType('open_webui.utils.tools')
    tool_utils.get_async_tool_function_and_apply_extra_params = AsyncMock(return_value=lambda: None)
    tool_utils.get_builtin_function_introspection = lambda _function: {}
    tool_utils.get_builtin_tool_spec = lambda function: {
        'name': function.__name__, 'description': '', 'parameters': {'type': 'object'}
    }
    monkeypatch.setitem(sys.modules, 'open_webui.utils.tools', tool_utils)
    tool_search = import_module('open_webui.utils.tool_search')

    monkeypatch.setattr(
        tool_search,
        'get_tool_search_config',
        AsyncMock(return_value={'defer_threshold': 1, 'always_loaded': [], 'defer_builtin_tools': True}),
    )

    builtin_module = types.ModuleType('open_webui.tools.builtin')

    async def search_tools(query: str):
        return []

    builtin_module.search_tools = search_tools
    monkeypatch.setitem(sys.modules, 'open_webui.tools.builtin', builtin_module)

    required_tools = {'list_editable_documents', 'start_document_summary_task', 'get_document_task'}
    tools = {
        name: {'type': 'builtin', 'spec': {'name': name, 'description': 'Large schema' * 10}}
        for name in required_tools | {'other_large_tool'}
    }
    # Tool resolution runs before evidence retrieval sets resource_summary_task_required.
    metadata = {
        'resource_aware_files': True,
        'resource_execution_requested': True,
        'resource_context_mode': 'HIERARCHICAL',
        'resource_scope_ids': ['upload-1'],
        'agent_task_state': {
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'status': 'in_progress',
        },
    }

    deferred = await tool_search.apply_tool_search({'messages': []}, metadata, tools)

    assert deferred == {'other_large_tool'}
    assert not required_tools.intersection(deferred)
    sys.modules.pop('open_webui.utils.tool_search', None)
