"""Conservative provider/model capabilities for resource-aware chat."""

from dataclasses import dataclass
import json
import os
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelCapabilities:
    supports_tools: bool = False
    parallel_tool_calls: bool = False
    context_window_tokens: int = 16_000
    max_output_tokens: int = 4_096


def _profile(supports_tools: bool, parallel_tool_calls: bool = False) -> ModelCapabilities:
    # Context and output defaults stay conservative until the provider/model
    # owner supplies exact values through model metadata or environment config.
    return ModelCapabilities(
        supports_tools=supports_tools,
        parallel_tool_calls=parallel_tool_calls,
    )


_ONE_MIN_PROFILES = {
    'qwen3.7-plus': _profile(True, True),
    'qwen3.7-max': _profile(True, True),
    'qwen-plus': _profile(True, True),
    'deepseek-v4-pro': _profile(True),
    'deepseek-v4-flash': _profile(True),
    'deepseek-flash': _profile(True),
    'gpt-5.6': _profile(True),
    'gpt-6-sol': _profile(True),
    'gpt-6-luna': _profile(True),
}
_COMMON_PROFILES = {
    'gpt-4.1': _profile(True, True),
    'gpt-4.1-mini': _profile(True, True),
    'gpt-4o': _profile(True, True),
    'gpt-4o-mini': _profile(True, True),
    'o3': _profile(True, True),
    'o3-mini': _profile(True, True),
    'o4-mini': _profile(True, True),
    'gpt-5.6': _profile(True),
    'gpt-6-sol': _profile(True),
    'gpt-6-luna': _profile(True),
}


def resolve_model_capabilities(
    model_id: str, base_url: str, overrides: dict | None = None
) -> ModelCapabilities:
    parsed = urlparse(base_url)
    normalized_model = model_id.rsplit('/', 1)[-1].strip().lower()
    one_min = parsed.hostname == 'api.1min.ai' and parsed.path.rstrip('/') == '/openai/v1'
    profile = _ONE_MIN_PROFILES.get(normalized_model) if one_min else None
    if profile is None:
        profile = _COMMON_PROFILES.get(normalized_model)
    overrides = dict(overrides or {})
    try:
        configured = json.loads(os.getenv('MH_MODEL_CAPABILITIES_JSON', '{}'))
        if isinstance(configured, dict):
            provider_key = f'{parsed.hostname or "default"}/{normalized_model}'
            overrides = {**configured.get(normalized_model, {}), **configured.get(provider_key, {}), **overrides}
    except (json.JSONDecodeError, TypeError):
        pass
    tool_setting = overrides.get('tool_calling', overrides.get('function_calling'))
    supports_tools = profile.supports_tools if tool_setting is None and profile else bool(tool_setting)
    context_window = overrides.get(
        'context_window_tokens', profile.context_window_tokens if profile else 16_000
    )
    output_limit = overrides.get(
        'max_output_tokens', profile.max_output_tokens if profile else 4_096
    )
    return ModelCapabilities(
        supports_tools=supports_tools,
        parallel_tool_calls=(
            bool(overrides.get('parallel_tool_calls', profile.parallel_tool_calls if profile else False))
            if supports_tools else False
        ),
        context_window_tokens=int(context_window or 16_000),
        max_output_tokens=int(output_limit or 4_096),
    )
