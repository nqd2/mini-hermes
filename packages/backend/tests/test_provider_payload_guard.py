import pytest

from open_webui.mini_hermes.provider_guard import (
    ProviderCapabilityUnsupported, ProviderPayloadTooLarge, apply_capability_constraints,
    validate_chat_payload, validate_provider_payload,
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


def test_context_budget_uses_serialized_payload_and_reserved_output():
    payload = {'model': 'custom-model', 'messages': [{'role': 'user', 'content': 'x' * 110_000}]}
    with pytest.raises(ProviderPayloadTooLarge) as exc:
        validate_provider_payload(JSONCodec.dumps(payload), payload, 'https://example.com/v1')
    assert exc.value.detail['code'] == 'model_context_budget_exceeded'
    assert exc.value.detail['reserved_output_tokens'] == 4096


def test_unknown_model_rejects_tool_schemas_with_structured_error():
    payload = {'model': 'unregistered-model', 'tools': [{'type': 'function', 'function': {'name': 'read'}}]}
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
