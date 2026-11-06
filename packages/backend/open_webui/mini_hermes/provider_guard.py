"""Validate the exact serialized chat body before an OpenAI-compatible send."""

from urllib.parse import urlparse

from fastapi import HTTPException

from open_webui.utils.json_codec import JSONCodec


class ProviderPayloadTooLarge(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(status_code=413, detail=detail)


class ProviderCapabilityUnsupported(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(status_code=400, detail=detail)


def apply_capability_constraints(payload: dict, base_url: str, overrides: dict | None = None) -> dict:
    """Omit optional tools for unsupported models and reject forced tool calls."""
    from .model_capabilities import resolve_model_capabilities

    model = str(payload.get('model') or '')
    capabilities = resolve_model_capabilities(model, base_url, overrides)
    tools = payload.get('tools') or []
    if tools and not capabilities.supports_tools:
        tool_choice = payload.get('tool_choice')
        if tool_choice not in (None, 'auto'):
            raise ProviderCapabilityUnsupported({
                'code': 'provider_model_does_not_support_tools',
                'message': f'Model {model} is not configured for tool calling.',
                'model': model,
            })
        payload.pop('tools', None)
    if not capabilities.supports_tools:
        payload.pop('tool_choice', None)
    if not capabilities.parallel_tool_calls:
        payload.pop('parallel_tool_calls', None)
    for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
        try:
            requested_output = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if requested_output > capabilities.max_output_tokens:
            raise ProviderPayloadTooLarge({
                'code': 'model_output_limit_exceeded',
                'message': f'Requested {key} exceeds the configured output limit for model {model}.',
                'model': model,
                'param': key,
                'requested_tokens': requested_output,
                'max_output_tokens': capabilities.max_output_tokens,
            })
    return payload


def validate_chat_payload(
    wire_body: str, payload: dict, base_url: str, *, max_chars: int = 95_000
) -> dict:
    """Return size metrics; reject requests beyond the 1min.AI operating cap."""
    messages = payload.get('messages')
    if messages is None:
        request_input = payload.get('input')
        messages = request_input if isinstance(request_input, list) else ([request_input] if request_input else [])
    messages = messages or []
    tools = payload.get('tools') or []
    metrics = {
        'serialized_chars': len(wire_body),
        'message_count': len(messages),
        'message_chars': len(JSONCodec.dumps(messages)),
        'tool_chars': len(JSONCodec.dumps(tools)),
    }
    parsed = urlparse(base_url)
    if parsed.hostname == 'api.1min.ai' and parsed.path.rstrip('/') == '/openai/v1':
        if metrics['serialized_chars'] > max_chars or metrics['message_count'] > 200:
            raise ProviderPayloadTooLarge(
                {
                    'code': 'provider_payload_too_large',
                    'message': (
                        'The prepared model request exceeds the 1min.AI size limit. '
                        'Read fewer file sections or compact the conversation.'
                    ),
                    'limit_chars': max_chars,
                    **metrics,
                }
            )
    return metrics


def validate_provider_payload(
    wire_body: str | bytes, payload: dict, base_url: str, capabilities: dict | None = None
) -> dict:
    """Validate any JSON model request using the already-serialized wire body."""
    if isinstance(wire_body, bytes):
        serialized = wire_body.decode('utf-8', errors='replace')
    else:
        serialized = wire_body
    metrics = validate_chat_payload(serialized, payload, base_url)
    metrics['serialized_bytes'] = len(serialized.encode('utf-8'))
    from .context_budget import validate_context_budget

    metrics.update(validate_context_budget(serialized, payload, base_url, capabilities))
    return metrics
