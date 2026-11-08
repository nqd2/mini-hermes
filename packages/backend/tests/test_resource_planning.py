from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.mini_hermes import model_capabilities, resource_aware, resource_tasks
from open_webui.mini_hermes.execution import pipeline
from open_webui.utils.chat_runtime.resource_planning import prepare_resource_execution


@pytest.mark.asyncio
async def test_prepare_resource_execution_skips_capability_and_task_lookups_when_disabled(monkeypatch):
    monkeypatch.setattr(resource_aware, 'resource_aware_context_enabled', lambda *_args, **_kwargs: False)
    config_get = AsyncMock()
    request = SimpleNamespace(state=SimpleNamespace())
    metadata = {'resource_history_files': []}

    result = await prepare_resource_execution(request, metadata, 'hello', object(), {}, config_get)

    assert result is None
    assert metadata['resource_aware_files'] is False
    config_get.assert_not_awaited()
    assert not hasattr(request.state, 'resource_model_capabilities')


@pytest.mark.asyncio
async def test_prepare_resource_execution_resolves_provider_limits_and_initializes_pipeline(monkeypatch):
    monkeypatch.setattr(resource_aware, 'resource_aware_context_enabled', lambda *_args, **_kwargs: True)
    pending_tasks = AsyncMock(return_value=['task-1'])
    monkeypatch.setattr(resource_tasks, 'pending_tasks_for_chat', pending_tasks)
    capability = SimpleNamespace(
        supports_tools=False,
        parallel_tool_calls=False,
        context_window_tokens=64_000,
        max_output_tokens=4_000,
    )
    resolve_args = []

    def resolve_capabilities(*args):
        resolve_args.append(args)
        return capability

    monkeypatch.setattr(model_capabilities, 'resolve_model_capabilities', resolve_capabilities)
    pipeline_calls = []

    class FakePipeline:
        def resolve(self, metadata, prompt, resolved_capability, *, pending_task_ids):
            pipeline_calls.append((metadata, prompt, resolved_capability, pending_task_ids))

    monkeypatch.setattr(pipeline, 'ResourceRequestPipeline', FakePipeline)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'chat-model': {'urlIdx': 1}})),
        state=SimpleNamespace(),
    )
    user = SimpleNamespace(id='alice')
    metadata = {'chat_id': 'chat-1', 'resource_history_files': [{'id': 'file-1'}]}
    config_get = AsyncMock(return_value=['https://provider-a.test', 'https://provider-b.test'])
    model = {'id': 'chat-model', 'info': {'meta': {'capabilities': {'parallel_tool_calls': True}}}}

    result = await prepare_resource_execution(request, metadata, 'summarize file', user, model, config_get)

    assert result is capability
    assert config_get.await_args.args == ('openai.api_base_urls',)
    pending_tasks.assert_awaited_once_with('chat-1', 'alice')
    assert resolve_args == [
        ('chat-model', 'https://provider-b.test', {'parallel_tool_calls': True}),
    ]
    assert metadata['resource_aware_files'] is True
    assert metadata['resource_model_tools'] is False
    assert pipeline_calls == [(metadata, 'summarize file', capability, ['task-1'])]
    assert request.state.resource_model_capabilities == {
        'tool_calling': False,
        'parallel_tool_calls': False,
        'context_window_tokens': 64_000,
        'max_output_tokens': 4_000,
    }
