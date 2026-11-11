"""Bound model-facing tool results without discarding their full source value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_webui.mini_hermes.context_budget import estimate_tokens
from open_webui.utils.json_codec import JSONCodec


@dataclass(frozen=True, slots=True)
class BoundedToolOutput:
    value: Any
    text: str
    original_chars: int
    original_tokens: int
    truncated: bool
    artifact_id: str | None = None


class ToolOutputManager:
    def __init__(self, max_tokens: int = 10_000, *, max_chars: int = 250_000):
        if max_tokens < 1 or max_chars < 1:
            raise ValueError('tool output limits must be positive')
        self.max_tokens = max_tokens
        self.max_chars = max_chars

    def truncate(
        self,
        value: Any,
        *,
        model: str = '',
        artifact_ref: dict | None = None,
        max_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> BoundedToolOutput:
        token_limit, char_limit = self._effective_limits(max_tokens, max_chars)
        if isinstance(value, str):
            text = value
        else:
            text = JSONCodec.dumps(value, ensure_ascii=False)
        original_chars = len(text)
        original_tokens = estimate_tokens(text, model)
        if original_chars <= char_limit and original_tokens <= token_limit:
            return BoundedToolOutput(
                value=value, text=text, original_chars=original_chars, original_tokens=original_tokens, truncated=False
            )

        artifact_id = artifact_ref.get('id') if artifact_ref else None
        marker_details = [f'original_chars={original_chars}', f'original_tokens={original_tokens}']
        if artifact_id:
            marker_details.extend([f'artifact_id={artifact_id}', 'read_agent_artifact'])
        markers = [f'\n[output truncated; {"; ".join(marker_details)}]\n']
        if artifact_id:
            markers.extend(
                [
                    f'\n[output truncated; chars={original_chars}; artifact_id={artifact_id}]\n',
                    f'\n[output truncated; chars={original_chars}; artifact_id={artifact_id}; read_agent_artifact]\n',
                    f'\n[output truncated; original_chars={original_chars}; '
                    f'artifact_id={artifact_id}; read_agent_artifact]\n',
                    f'\n[output truncated; artifact_id={artifact_id}; read_agent_artifact]\n',
                    f'\n[output truncated; artifact_id={artifact_id}]\n',
                ]
            )
        else:
            markers.append(f'\n[output truncated; chars={original_chars}]\n')
        marker = next(
            (item for item in markers if len(item) <= char_limit and estimate_tokens(item, model) <= token_limit),
            None,
        )
        if marker is None:
            raise ValueError('tool output budget is too small to preserve truncation metadata')
        budget = min(char_limit, max(token_limit * 4, len(marker)))
        low, high = 0, len(text)
        best = marker if estimate_tokens(marker, model) <= token_limit else ''
        while low <= high:
            shown = (low + high) // 2
            prefix_size = (shown + 1) // 2
            suffix_size = shown // 2
            candidate = text[:prefix_size] + marker + (text[-suffix_size:] if suffix_size else '')
            if len(candidate) <= budget and estimate_tokens(candidate, model) <= token_limit:
                best = candidate
                low = shown + 1
            else:
                high = shown - 1
        return BoundedToolOutput(
            value=best, text=best, original_chars=original_chars, original_tokens=original_tokens, truncated=True
        )

    async def process(
        self,
        value: Any,
        *,
        model: str = '',
        artifacts: Any | None = None,
        owner_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        max_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> BoundedToolOutput:
        token_limit, char_limit = self._effective_limits(max_tokens, max_chars)
        raw_text = value if isinstance(value, str) else JSONCodec.dumps(value, ensure_ascii=False)
        original_tokens = estimate_tokens(raw_text, model)
        if len(raw_text) <= char_limit and original_tokens <= token_limit:
            return BoundedToolOutput(
                value=value,
                text=raw_text,
                original_chars=len(raw_text),
                original_tokens=original_tokens,
                truncated=False,
            )
        artifact_ref = None
        if artifacts is not None and owner_id:
            artifact = await artifacts.put(
                owner_id,
                'tool-output',
                raw_text.encode('utf-8'),
                'text/plain; charset=utf-8',
                session_id=session_id,
                turn_id=turn_id,
                metadata={'kind': 'tool_output', 'model': model},
            )
            artifact_ref = {'id': artifact.id, 'size_bytes': artifact.size_bytes, 'sha256': artifact.sha256}
        bounded = self.truncate(
            value,
            model=model,
            artifact_ref=artifact_ref,
            max_tokens=token_limit,
            max_chars=char_limit,
        )
        return BoundedToolOutput(
            value=bounded.value,
            text=bounded.text,
            original_chars=bounded.original_chars,
            original_tokens=bounded.original_tokens,
            truncated=bounded.truncated,
            artifact_id=(artifact_ref or {}).get('id'),
        )

    def _effective_limits(self, max_tokens: int | None, max_chars: int | None) -> tuple[int, int]:
        token_limit = min(self.max_tokens, max_tokens) if max_tokens is not None else self.max_tokens
        char_limit = min(self.max_chars, max_chars) if max_chars is not None else self.max_chars
        if token_limit < 1 or char_limit < 1:
            raise ValueError('tool output limits must be positive')
        return token_limit, char_limit
