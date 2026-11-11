from types import SimpleNamespace

import pytest

from open_webui.mini_hermes.provider_guard import (
    ProviderCapabilityUnsupported,
    ProviderPayloadTooLarge,
    apply_capability_constraints,
    validate_chat_payload,
    validate_provider_payload,
)
from open_webui.utils.json_codec import JSONCodec

BASE_URL = 'https://api.1min.ai/openai/v1'


def test_guard_counts_the_actual_serialized_payload_with_unicode_and_tools():
    payload = {
        'model': 'qwen3.7-plus',
        'messages': [{'role': 'user', 'content': 'ngành ' * 20}],
        'tools': [{'type': 'function', 'function': {'name': 'read', 'description': 'ô' * 30}}],
    }
    wire_body = JSONCodec.dumps(payload)

    result = validate_chat_payload(wire_body, payload, BASE_URL, max_chars=len(wire_body))

    assert result['serialized_chars'] == len(wire_body)
    assert result['message_count'] == 1
    assert result['tool_chars'] > 0


def test_guard_rejects_a_large_1min_request_before_http_send():
    payload = {'model': 'qwen3.7-plus', 'messages': [{'role': 'user', 'content': 'x' * 100_000}]}
    wire_body = JSONCodec.dumps(payload)

    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_chat_payload(wire_body, payload, BASE_URL)

    assert exc.value.detail['code'] == 'provider_payload_too_large'
    assert exc.value.detail['serialized_chars'] == len(wire_body)


def test_guard_rejects_more_than_200_messages():
    payload = {'model': 'qwen3.7-plus', 'messages': [{'role': 'user', 'content': 'x'}] * 201}

    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_chat_payload(JSONCodec.dumps(payload), payload, BASE_URL)

    assert exc.value.detail['message_count'] == 201


def test_non_1min_provider_has_no_1min_character_limit():
    payload = {'model': 'other', 'messages': [{'role': 'user', 'content': 'x' * 100_000}]}
    wire_body = JSONCodec.dumps(payload)

    assert validate_chat_payload(wire_body, payload, 'https://example.com/v1')['serialized_chars'] == len(wire_body)


def test_context_budget_uses_serialized_payload_and_reserved_output(monkeypatch):
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_CHARS', '100000')
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_BYTES', '380000')
    payload = {'model': 'custom-model', 'messages': [{'role': 'user', 'content': 'x' * 96_000}]}
    wire_body = JSONCodec.dumps(payload)
    assert len(wire_body) <= 100_000
    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_provider_payload(wire_body, payload, 'https://example.com/v1')
    assert exc.value.detail['code'] == 'model_context_budget_exceeded'
    assert exc.value.detail['reserved_output_tokens'] == 4096


def test_unknown_model_rejects_tool_schemas_with_structured_error():
    payload = {
        'model': 'unregistered-model',
        'tools': [{'type': 'function', 'function': {'name': 'read'}}],
        'tool_choice': 'required',
    }
    with pytest.raises(ProviderCapabilityUnsupported) as exc:
        apply_capability_constraints(payload, 'https://provider.example/v1')
    assert exc.value.detail['code'] == 'provider_model_does_not_support_tools'


def test_unknown_model_drops_optional_tools_instead_of_breaking_chat():
    payload = {
        'model': 'unregistered-model',
        'tools': [{'type': 'function', 'function': {'name': 'read'}}],
        'tool_choice': 'auto',
        'parallel_tool_calls': True,
    }

    constrained = apply_capability_constraints(payload, 'https://provider.example/v1')

    assert 'tools' not in constrained
    assert 'tool_choice' not in constrained
    assert 'parallel_tool_calls' not in constrained


def test_unknown_model_still_rejects_a_required_tool_call():
    payload = {
        'model': 'unregistered-model',
        'tools': [{'type': 'function', 'function': {'name': 'read'}}],
        'tool_choice': 'required',
    }

    with pytest.raises(ProviderCapabilityUnsupported) as exc:
        apply_capability_constraints(payload, 'https://provider.example/v1')

    assert exc.value.detail['code'] == 'provider_model_does_not_support_tools'


def test_model_without_parallel_calls_does_not_receive_that_parameter():
    payload = {
        'model': 'deepseek-v4-pro',
        'tools': [{'type': 'function', 'function': {'name': 'read'}}],
        'parallel_tool_calls': False,
    }
    constrained = apply_capability_constraints(payload, BASE_URL)
    assert constrained['tools']
    assert 'parallel_tool_calls' not in constrained


def test_provider_guard_rejects_output_limit_above_model_capability():
    payload = {'model': 'deepseek-v4-pro', 'max_tokens': 5_000}
    with pytest.raises(ProviderPayloadTooLarge) as exc:
        apply_capability_constraints(payload, BASE_URL)
    assert exc.value.detail['code'] == 'model_output_limit_exceeded'
    assert exc.value.detail['param'] == 'max_tokens'


def test_global_request_character_budget_applies_to_every_provider(monkeypatch):
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_CHARS', '160')
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_BYTES', '640')
    payload = {'model': 'custom-model', 'messages': [{'role': 'user', 'content': 'x' * 200}]}

    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_provider_payload(JSONCodec.dumps(payload), payload, 'https://provider.example/v1')

    assert exc.value.detail['code'] == 'request_json_char_limit_exceeded'
    assert exc.value.detail['limit_chars'] == 160


def test_global_request_byte_budget_is_independent_of_character_budget(monkeypatch):
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_CHARS', '1000')
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_BYTES', '100')
    wire_body = '{"model":"custom-model","messages":[{"role":"user","content":"' + ('界' * 40) + '"}]}'
    payload = {'model': 'custom-model', 'messages': [{'role': 'user', 'content': '界' * 40}]}

    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_provider_payload(wire_body, payload, 'https://provider.example/v1')

    assert exc.value.detail['code'] == 'request_json_byte_limit_exceeded'
    assert exc.value.detail['serialized_chars'] < exc.value.detail['limit_chars']
    assert exc.value.detail['serialized_bytes'] > exc.value.detail['limit_bytes']


@pytest.mark.asyncio
async def test_ollama_generation_request_is_checked_before_network_send(monkeypatch):
    from open_webui.routers import ollama

    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_CHARS', '160')
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_BYTES', '640')

    async def unexpected_session():
        raise AssertionError('provider session was opened before request validation')

    monkeypatch.setattr(ollama, 'get_session', unexpected_session)
    payload = {'model': 'custom-model', 'messages': [{'role': 'user', 'content': 'x' * 200}]}

    with pytest.raises(ProviderPayloadTooLarge) as exc:
        await ollama.send_request(
            'http://ollama.example/v1/chat/completions',
            payload=JSONCodec.dumps(payload),
        )

    assert exc.value.detail['code'] == 'request_json_char_limit_exceeded'


@pytest.mark.asyncio
async def test_anthropic_passthrough_validates_the_serialized_body_before_send(monkeypatch):
    from importlib import import_module

    main = import_module('open_webui.main')

    payload = {
        'model': 'claude-test',
        'max_tokens': 32,
        'messages': [{'role': 'user', 'content': 'hello'}],
    }
    observed = {'order': []}

    async def request_target(*_args, **_kwargs):
        return 'claude-test', payload, 'https://provider.example/v1', 'secret', {}, {}

    class Response:
        headers = {'Content-Type': 'application/json'}
        status = 200

        async def json(self):
            return {'ok': True}

    class Session:
        async def request(self, **kwargs):
            observed['order'].append('send')
            observed['request'] = kwargs
            return Response()

    def validate(wire_body, request_payload, base_url, capabilities=None):
        observed['order'].append('validate')
        observed['wire_body'] = wire_body
        observed['payload'] = request_payload
        observed['base_url'] = base_url
        return {'message_count': len(request_payload.get('messages', [])), 'serialized_chars': len(wire_body)}

    async def get_session():
        return Session()

    async def cleanup_response(_response):
        return None

    monkeypatch.setattr(main.openai, 'get_anthropic_request_target', request_target)
    monkeypatch.setattr(main, 'get_session', get_session)
    monkeypatch.setattr(main, 'cleanup_response', cleanup_response)
    monkeypatch.setattr(
        'open_webui.mini_hermes.provider_guard.validate_provider_payload',
        validate,
    )

    response = await main.passthrough_anthropic_messages(
        SimpleNamespace(state=SimpleNamespace()),
        {},
        SimpleNamespace(),
    )

    expected_body = JSONCodec.dumps(payload)
    assert response == {'ok': True}
    assert observed['order'] == ['validate', 'send']
    assert observed['wire_body'] == expected_body
    assert observed['request']['data'] == expected_body
