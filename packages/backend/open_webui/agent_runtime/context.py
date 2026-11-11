"""Bounded context reconstruction for model requests."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any

from open_webui.mini_hermes.context_budget import estimate_tokens
from open_webui.mini_hermes.model_capabilities import resolve_model_capabilities
from open_webui.mini_hermes.provider_errors import ProviderPayloadTooLarge
from open_webui.utils.json_codec import JSONCodec

from .request_budget import RequestBudgetExceeded, RequestBudgetManager


@dataclass(frozen=True, slots=True)
class PreparedContext:
    payload: dict[str, Any]
    wire_body: str
    metrics: dict[str, Any]


class ContextManager:
    """Validate final JSON size and compact only complete old conversation turns."""

    def __init__(
        self,
        request_budget: RequestBudgetManager | None = None,
        *,
        compact_at_fraction: float | None = None,
    ):
        if compact_at_fraction is None:
            raw_fraction = os.getenv('AGENT_CONTEXT_COMPACT_FRACTION', '0.75')
            try:
                compact_at_fraction = float(raw_fraction)
            except ValueError as error:
                raise ValueError('AGENT_CONTEXT_COMPACT_FRACTION must be between 0 and 1') from error
        if not 0 < compact_at_fraction <= 1:
            raise ValueError('compact_at_fraction must be in (0, 1]')
        self.request_budget = request_budget or RequestBudgetManager()
        self.compact_at_fraction = compact_at_fraction

    def prepare(
        self,
        payload: dict[str, Any],
        base_url: str,
        capability_overrides: dict | None = None,
    ) -> PreparedContext:
        from open_webui.mini_hermes.provider_guard import apply_capability_constraints, validate_chat_payload

        if not isinstance(payload, dict):
            raise TypeError('model payload must be a JSON object')
        current = copy.deepcopy(payload)
        model = str(current.get('model') or '')
        capabilities = resolve_model_capabilities(model, base_url, capability_overrides)
        requested_output = _requested_output_tokens(current, capabilities.max_output_tokens)
        input_limit = max(1, capabilities.context_window_tokens - requested_output)
        proactive_limit = max(1, int(input_limit * self.compact_at_fraction))
        compacted_count = 0
        compacted_for_context = False
        accumulated_summary = ''

        # One old turn is removed per pass. This keeps the transaction small and
        # makes every intermediate context a valid, ordered conversation.
        while True:
            constrained = apply_capability_constraints(copy.deepcopy(current), base_url, capability_overrides)
            wire_body = JSONCodec.dumps(constrained)
            size_metrics = self.request_budget.measure(wire_body)
            input_tokens = estimate_tokens(wire_body, model)
            hard_overflow = (
                size_metrics['serialized_chars'] > self.request_budget.limits.max_json_chars
                or size_metrics['serialized_bytes'] > self.request_budget.limits.max_json_bytes
            )
            preparation_overflow = (
                size_metrics['serialized_chars'] > self.request_budget.limits.preparation_limit_chars
            )
            token_overflow = input_tokens > input_limit
            should_compact = hard_overflow or preparation_overflow or token_overflow or input_tokens > proactive_limit

            if not should_compact:
                provider_metrics = validate_chat_payload(wire_body, constrained, base_url)
                return PreparedContext(
                    payload=constrained,
                    wire_body=wire_body,
                    metrics={
                        **provider_metrics,
                        **size_metrics,
                        'estimated_input_tokens': input_tokens,
                        'context_window_tokens': capabilities.context_window_tokens,
                        'reserved_output_tokens': requested_output,
                        'preparation_limit_chars': self.request_budget.limits.preparation_limit_chars,
                        'context_compaction_threshold_tokens': proactive_limit,
                        'compacted_messages': compacted_count,
                        'compacted_for_context': compacted_for_context,
                        'compacted_for_request_size': hard_overflow,
                    },
                )

            candidate = _drop_oldest_complete_turn(current, accumulated_summary)
            if candidate is not None:
                current, dropped_count, accumulated_summary = candidate
                compacted_count += dropped_count
                compacted_for_context = compacted_for_context or token_overflow or input_tokens > proactive_limit
                continue

            if hard_overflow:
                try:
                    self.request_budget.validate(wire_body)
                except RequestBudgetExceeded as error:
                    raise ProviderPayloadTooLarge(error.detail) from error
            if preparation_overflow:
                raise ProviderPayloadTooLarge(
                    {
                        'code': 'request_preparation_char_limit_exceeded',
                        'message': (
                            'The prepared model request exceeds the internal context preparation limit. '
                            'Read fewer sections or compact earlier conversation turns.'
                        ),
                        'preparation_limit_chars': self.request_budget.limits.preparation_limit_chars,
                        'max_json_chars': self.request_budget.limits.max_json_chars,
                        **size_metrics,
                    }
                )
            if token_overflow:
                raise ProviderPayloadTooLarge(
                    {
                        'code': 'model_context_budget_exceeded',
                        'message': (
                            'The required model instructions and current user input exceed this model’s input budget.'
                        ),
                        'estimated_input_tokens': input_tokens,
                        'context_window_tokens': capabilities.context_window_tokens,
                        'reserved_output_tokens': requested_output,
                        'available_input_tokens': input_limit,
                    }
                )
            # If no whole user turn can be removed, retain the sole active turn.
            provider_metrics = validate_chat_payload(wire_body, constrained, base_url)
            return PreparedContext(
                payload=constrained,
                wire_body=wire_body,
                metrics={
                    **provider_metrics,
                    **size_metrics,
                    'estimated_input_tokens': input_tokens,
                    'context_window_tokens': capabilities.context_window_tokens,
                    'reserved_output_tokens': requested_output,
                    'preparation_limit_chars': self.request_budget.limits.preparation_limit_chars,
                    'context_compaction_threshold_tokens': proactive_limit,
                    'compacted_messages': compacted_count,
                    'compacted_for_context': compacted_for_context,
                    'compacted_for_request_size': False,
                    'context_threshold_exceeded': input_tokens > proactive_limit,
                },
            )


def _requested_output_tokens(payload: dict, default: int) -> int:
    requested = []
    for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
        try:
            value = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            requested.append(value)
    return max(requested) if requested else default


def _drop_oldest_complete_turn(payload: dict, existing_summary: str) -> tuple[dict, int, str] | None:
    """Drop a prefix ending at the second user message, never a tool-call fragment."""
    field = 'messages' if isinstance(payload.get('messages'), list) else 'input'
    items = payload.get(field)
    if not isinstance(items, list):
        return None

    prefix_end = 0
    while prefix_end < len(items) and _is_instruction_message(items[prefix_end]):
        prefix_end += 1
    user_boundaries = [
        index for index, item in enumerate(items[prefix_end:], start=prefix_end) if _is_user_message(item)
    ]
    if len(user_boundaries) < 2:
        return None

    boundary = user_boundaries[1]
    dropped = items[prefix_end:boundary]
    instructions = [item for item in items[:prefix_end] if not _is_generated_summary(item)]
    retained = instructions + items[boundary:]
    summary = _merge_summary(existing_summary, dropped)
    updated = copy.deepcopy(payload)
    updated[field] = retained
    if field == 'messages':
        insertion = len(instructions)
        updated[field].insert(insertion, {'role': 'system', 'content': summary})
    else:
        instructions = (
            str(updated.get('instructions') or '')
            .rsplit('\n\nEarlier conversation summary (context reset): ', 1)[0]
            .strip()
        )
        updated['instructions'] = f'{instructions}\n\n{summary}'.strip()
    return updated, len(dropped), summary


def _is_instruction_message(item: Any) -> bool:
    return isinstance(item, dict) and item.get('role') in {'system', 'developer'}


def _is_generated_summary(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get('role') == 'system'
        and isinstance(item.get('content'), str)
        and item['content'].startswith('Earlier conversation summary (context reset): ')
    )


def _is_user_message(item: Any) -> bool:
    return isinstance(item, dict) and item.get('role') == 'user'


def _merge_summary(previous: str, dropped: list[Any], max_chars: int = 1200) -> str:
    snippets = []
    for item in dropped:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in {'user', 'assistant'}:
            continue
        content = item.get('content') or item.get('text') or ''
        if isinstance(content, list):
            content = ' '.join(str(part.get('text') or '') for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            calls = item.get('tool_calls') or []
            content = 'Tool calls: ' + ', '.join(
                str((call.get('function') or {}).get('name') or call.get('name') or 'tool')
                for call in calls
                if isinstance(call, dict)
            )
        text = ' '.join(content.split())
        if text:
            snippets.append(f'{role}: {text[:220]}')
    if previous:
        snippets.insert(0, previous.removeprefix('Earlier conversation summary (context reset): '))
    rendered = 'Earlier conversation summary (context reset): ' + ' | '.join(snippets)
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 1].rsplit(' ', 1)[0] + '…'
    return rendered
