from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware
from open_webui.utils.chat_runtime import legacy_tool_calling


@pytest.mark.asyncio
async def test_legacy_tool_calling_filters_arguments_runs_all_calls_and_collects_sources(monkeypatch):
    lookup = AsyncMock()
    file_handler = AsyncMock()
    tools = {
        'find_program': {
            'tool_id': 'catalog',
            'spec': {
                'parameters': {
                    'properties': {'query': {}, 'limit': {}},
                }
            },
            'callable': lookup,
        },
        'read_file': {
            'tool_id': 'files',
            'metadata': {'file_handler': True},
            'spec': {'parameters': {'properties': {'path': {}}}},
            'callable': file_handler,
        },
    }
    process_tool_result = AsyncMock(side_effect=lambda request, name, result, *args: (f'result:{name}', [], []))
    monkeypatch.setattr(
        legacy_tool_calling.Config,
        'get_many',
        AsyncMock(
            return_value={
                'task.model.default': 'task-model',
                'task.model.external': None,
                'task.tools.prompt_template': '',
            }
        ),
    )
    monkeypatch.setattr(
        legacy_tool_calling,
        'get_task_model_id',
        lambda model, default, external, models: 'task-model',
    )
    monkeypatch.setattr(
        legacy_tool_calling,
        'tools_function_calling_generation_template',
        lambda template, specs: 'Tool prompt',
    )
    monkeypatch.setattr(
        legacy_tool_calling,
        'generate_chat_completion',
        AsyncMock(
            return_value={
                'choices': [
                    {
                        'message': {
                            'content': (
                                '{"tool_calls": ['
                                '{"name":"find_program","parameters":{"query":"CS","extra":1}},'
                                '{"name":"read_file","parameters":{"path":"report.xlsx","extra":2}}'
                                ']}'
                            )
                        }
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(legacy_tool_calling, 'process_tool_result', process_tool_result)
    body = {
        'model': 'chat-model',
        'messages': [{'role': 'user', 'content': 'Find CS program and read the report.'}],
        'metadata': {'files': [{'id': 'file-1'}]},
    }

    result_body, flags = await middleware.chat_completion_tools_handler(
        request=object(),
        body=body,
        extra_params={'__event_call__': None, '__event_emitter__': None, '__metadata__': {}},
        user=object(),
        models={},
        tools=tools,
    )

    lookup.assert_awaited_once_with(query='CS')
    file_handler.assert_awaited_once_with(path='report.xlsx')
    assert process_tool_result.await_count == 2
    assert 'files' not in result_body['metadata']
    assert [source['source']['name'] for source in flags['sources']] == ['catalog/find_program', 'files/read_file']
    assert [source['document'] for source in flags['sources']] == [['result:find_program'], ['result:read_file']]
