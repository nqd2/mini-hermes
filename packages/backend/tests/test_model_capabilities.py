from open_webui.mini_hermes.model_capabilities import resolve_model_capabilities


def test_known_1min_models_have_explicit_tool_profiles():
    assert resolve_model_capabilities('qwen3.7-plus', 'https://api.1min.ai/openai/v1').supports_tools
    deepseek = resolve_model_capabilities('deepseek-v4-pro', 'https://api.1min.ai/openai/v1')
    assert deepseek.supports_tools
    assert not deepseek.parallel_tool_calls


def test_unknown_model_fails_closed_for_tools():
    assert not resolve_model_capabilities('unknown-model', 'https://example.com/v1').supports_tools


def test_explicit_model_capabilities_override_provider_profile():
    profile = resolve_model_capabilities(
        'qwen3.7-plus', 'https://api.1min.ai/openai/v1',
        {'tool_calling': False, 'context_window_tokens': 32000, 'max_output_tokens': 2048},
    )
    assert not profile.supports_tools
    assert profile.context_window_tokens == 32000
    assert profile.max_output_tokens == 2048


def test_environment_registry_supports_provider_specific_context_limits(monkeypatch):
    monkeypatch.setenv(
        'MH_MODEL_CAPABILITIES_JSON',
        '{"api.1min.ai/custom-model":{"tool_calling":true,"parallel_tool_calls":false,'
        '"context_window_tokens":64000,"max_output_tokens":8000}}',
    )
    profile = resolve_model_capabilities('custom-model', 'https://api.1min.ai/openai/v1')
    assert profile.supports_tools
    assert not profile.parallel_tool_calls
    assert profile.context_window_tokens == 64000
    assert profile.max_output_tokens == 8000
