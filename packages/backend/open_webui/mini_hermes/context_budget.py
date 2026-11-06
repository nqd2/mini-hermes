"""Model-context validation shared by chat, tool-loop and proxy requests."""

from __future__ import annotations

import math
import re

from .model_capabilities import resolve_model_capabilities
from .provider_guard import ProviderPayloadTooLarge


def estimate_tokens(text: str, model: str) -> int:
    """Use the provider tokenizer when available; otherwise conservatively count text."""
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding('cl100k_base')
        return len(encoding.encode(text))
    except Exception:
        # CJK and other non-ASCII glyphs commonly consume at least one token each.
        non_ascii = sum(not char.isascii() for char in text)
        ascii_text = ''.join(char for char in text if char.isascii())
        lexical = sum(max(1, math.ceil(len(word) / 4)) for word in re.findall(r'\w+', ascii_text))
        punctuation = sum(not (char.isalnum() or char.isspace()) for char in ascii_text)
        return non_ascii + lexical + punctuation


def validate_context_budget(
    wire_body: str | bytes, payload: dict, base_url: str, overrides: dict | None = None
) -> dict:
    serialized = wire_body.decode('utf-8', errors='replace') if isinstance(wire_body, bytes) else wire_body
    model = str(payload.get('model') or '')
    capabilities = resolve_model_capabilities(model, base_url, overrides)
    input_tokens = estimate_tokens(serialized, model)
    requested_output = 0
    for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
        try:
            requested_output = max(requested_output, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            continue
    if requested_output > capabilities.max_output_tokens:
        raise ProviderPayloadTooLarge({
            'code': 'model_output_limit_exceeded',
            'message': 'Requested output exceeds the configured model output limit.',
            'model': model,
            'requested_tokens': requested_output,
            'max_output_tokens': capabilities.max_output_tokens,
        })
    output_reserve = max(1, requested_output or capabilities.max_output_tokens)
    available_input_tokens = max(1, capabilities.context_window_tokens - output_reserve)
    metrics = {
        'estimated_input_tokens': input_tokens,
        'context_window_tokens': capabilities.context_window_tokens,
        'reserved_output_tokens': output_reserve,
    }
    if input_tokens > available_input_tokens:
        raise ProviderPayloadTooLarge({
            'code': 'model_context_budget_exceeded',
            'message': (
                'The prepared request exceeds this model’s input budget. '
                'Reduce file evidence or compact chat history.'
            ),
            **metrics,
        })
    return metrics
