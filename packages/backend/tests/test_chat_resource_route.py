import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline
from open_webui.mini_hermes.provider_guard import validate_chat_payload
from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate
from open_webui.utils.json_codec import JSONCodec


def _build_model(model_id: str) -> dict:
    return {
        'id': model_id,
        'name': model_id,
        'owned_by': 'openai',
        'direct': True,
        'info': {'meta': {'capabilities': {'tool_calling': False}}},
    }


def _install_model_configuration(monkeypatch, main, model_id: str, model: dict) -> None:
    monkeypatch.setattr(main.app.state, 'MODELS', {model_id: model}, raising=False)

    async def config_get(key, *_args, **_kwargs):
        values = {
            'models.default_params': {},
            'chat.tool_permissions.enable': False,
            'openai.api_base_urls': ['https://api.1min.ai/openai/v1'],
        }
        if key == 'rag.template':
            from open_webui.config import DEFAULT_RAG_TEMPLATE

            return DEFAULT_RAG_TEMPLATE
        return values.get(key)

    monkeypatch.setattr(main.Config, 'get', staticmethod(config_get))


def _install_chat_adapters(
    monkeypatch, main, middleware, resource_service, attachment, prompt, evidence_id
) -> list[dict]:
    async def search(owner_id, files, chat_id, query, **kwargs):
        assert owner_id == 'alice'
        assert files == [attachment]
        assert chat_id == ''
        assert query == prompt
        assert kwargs['scope_file_ids'] == ['upload-1']
        return [
            {
                'source': {'id': 'upload-1', 'name': 'programs.xlsx'},
                'document': ['Program 301 — Computer Science'],
                'metadata': [
                    {
                        'document_id': 'document-1',
                        'file_id': 'upload-1',
                        'revision': 1,
                        'start_offset': 1200,
                        'end_offset': 1233,
                        'total_chars': 4000,
                        'evidence_id': evidence_id,
                        'coverage_complete': True,
                    }
                ],
            }
        ]

    async def no_event_handler(*_args, **_kwargs):
        return None

    async def passthrough_pipeline(_request, form_data, _user, _models):
        return form_data

    async def no_system_prompt(*_args, **_kwargs):
        return None

    async def no_oauth_token(*_args, **_kwargs):
        return None

    monkeypatch.setattr(resource_service, 'search_chat_files', search)
    monkeypatch.setattr(middleware, 'ENABLE_FUNCTIONS', False)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', passthrough_pipeline)
    monkeypatch.setattr(middleware, 'get_event_emitter', no_event_handler)
    monkeypatch.setattr(middleware, 'get_event_call', no_event_handler)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', no_oauth_token)
    monkeypatch.setattr(middleware, 'get_task_model_id', lambda model, *_args: model)
    monkeypatch.setattr(middleware, 'resolve_system_prompt', no_system_prompt)

    provider_requests = []

    async def provider_transport(_request, form_data, _user):
        payload = {'model': form_data['model'], 'messages': form_data['messages']}
        wire_body = JSONCodec.dumps(payload, ensure_ascii=False)
        metrics = validate_chat_payload(wire_body, payload, 'https://api.1min.ai/openai/v1')
        assert metrics['serialized_chars'] == len(wire_body)
        assert evidence_id in wire_body
        assert metrics['serialized_chars'] < 95_000
        provider_requests.append(payload)
        return {'choices': [{'message': {'role': 'assistant', 'content': 'Program 301'}}]}

    async def build_context(_request, form_data, _user, _model, metadata, _tasks, events):
        return {'form_data': form_data, 'metadata': metadata, 'events': events}

    async def present_answer(_response, context):
        metadata = context['metadata']
        answer = {
            'type': 'message',
            'status': 'completed',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'Program 301'}],
        }
        visible = ResourceAnswerGate(metadata, enabled=True).present([answer], final=True)
        assert visible == [answer]
        assert ResourceRequestPipeline.decision(metadata).status == 'completed'
        return {'answer': 'Program 301', 'sources': metadata['sources']}

    monkeypatch.setattr(main, 'chat_completion_handler', provider_transport)
    monkeypatch.setattr(main, 'build_chat_response_context', build_context)
    monkeypatch.setattr(main, 'process_chat_response', present_answer)
    return provider_requests


def _build_request(app) -> Request:
    return Request(
        {
            'type': 'http',
            'http_version': '1.1',
            'method': 'POST',
            'scheme': 'http',
            'server': ('test', 80),
            'client': ('test', 1234),
            'root_path': '',
            'path': '/api/chat/completions',
            'raw_path': b'/api/chat/completions',
            'query_string': b'',
            'headers': [(b'user-agent', b'test')],
            'app': app,
            'state': {},
        }
    )


@pytest.mark.asyncio
async def test_chat_route_runs_bounded_resource_evidence_before_provider(monkeypatch):
    main = importlib.import_module('open_webui.main')
    middleware = importlib.import_module('open_webui.utils.middleware')
    resource_service = importlib.import_module('open_webui.mini_hermes.resource_service')

    prompt = 'Which program is related to Computer Science?'
    attachment = {'type': 'file', 'id': 'upload-1', 'name': 'programs.xlsx'}
    evidence_id = 'evidence-program-301'
    model_id = 'qwen3.7-plus'
    model = _build_model(model_id)
    _install_model_configuration(monkeypatch, main, model_id, model)
    provider_requests = _install_chat_adapters(
        monkeypatch, main, middleware, resource_service, attachment, prompt, evidence_id
    )

    result = await main.chat_completion(
        _build_request(main.app),
        {
            'model': model_id,
            'model_item': model,
            'messages': [{'role': 'user', 'content': prompt, 'files': [attachment]}],
            'files': [attachment],
            'user_message': {'id': 'user-message-1', 'content': prompt, 'files': [attachment]},
            'id': 'assistant-message-1',
            'params': {},
            'stream': False,
            'tools': [],
        },
        SimpleNamespace(id='alice', role='user', name='Alice'),
    )

    assert result['answer'] == 'Program 301'
    assert result['sources'][0]['metadata'][0]['evidence_id'] == evidence_id
    assert len(provider_requests) == 1


def test_chat_completion_http_route_binds_user_body_and_resource_files(monkeypatch):
    main = importlib.import_module('open_webui.main')
    middleware = importlib.import_module('open_webui.utils.middleware')
    resource_service = importlib.import_module('open_webui.mini_hermes.resource_service')

    prompt = 'Which program is related to Computer Science?'
    attachment = {'type': 'file', 'id': 'upload-1', 'name': 'programs.xlsx'}
    evidence_id = 'evidence-program-301-http'
    model_id = 'qwen3.7-plus'
    model = {**_build_model(model_id), 'direct': False}
    _install_model_configuration(monkeypatch, main, model_id, model)
    user = SimpleNamespace(id='alice', role='user', name='Alice')
    monkeypatch.setitem(main.app.dependency_overrides, main.get_verified_user, lambda: user)
    monkeypatch.setattr(main, 'BYPASS_MODEL_ACCESS_CONTROL', True)

    async def no_saved_model(_model_id):
        return None

    monkeypatch.setattr(main.Models, 'get_model_by_id', no_saved_model)
    provider_requests = _install_chat_adapters(
        monkeypatch, main, middleware, resource_service, attachment, prompt, evidence_id
    )

    response = TestClient(main.app).post(
        '/api/chat/completions',
        json={
            'model': model_id,
            'messages': [{'role': 'user', 'content': prompt, 'files': [attachment]}],
            'files': [attachment],
            'user_message': {'id': 'user-message-http', 'content': prompt, 'files': [attachment]},
            'id': 'assistant-message-http',
            'params': {},
            'stream': False,
            'tools': [],
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result['answer'] == 'Program 301'
    assert result['sources'][0]['metadata'][0]['evidence_id'] == evidence_id
    assert len(provider_requests) == 1
