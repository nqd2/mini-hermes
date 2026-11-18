"""Hard limits for the exact JSON body sent to a model provider."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException

DEFAULT_MAX_JSON_CHARS = 100_000
DEFAULT_MAX_PREPARATION_CHARS = 95_000
DEFAULT_MAX_JSON_BYTES = 380_000


class RequestBudgetExceeded(HTTPException):
    def __init__(self, detail: dict):
        super().__init__(status_code=413, detail=detail)


def _positive_limit(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be a positive integer') from exc
    if value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


@dataclass(frozen=True, slots=True)
class RequestBudgetLimits:
    max_json_chars: int = DEFAULT_MAX_JSON_CHARS
    max_preparation_chars: int = DEFAULT_MAX_PREPARATION_CHARS
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES

    def __post_init__(self) -> None:
        # Configuration may tighten the approved ceilings, never raise them.
        object.__setattr__(self, 'max_json_chars', min(self.max_json_chars, DEFAULT_MAX_JSON_CHARS))
        object.__setattr__(
            self, 'max_preparation_chars', min(self.max_preparation_chars, DEFAULT_MAX_PREPARATION_CHARS)
        )

    @classmethod
    def from_environment(cls) -> RequestBudgetLimits:
        return cls(
            max_json_chars=_positive_limit('AGENT_REQUEST_MAX_JSON_CHARS', DEFAULT_MAX_JSON_CHARS),
            max_preparation_chars=_positive_limit(
                'AGENT_REQUEST_PREPARATION_MAX_JSON_CHARS', DEFAULT_MAX_PREPARATION_CHARS
            ),
            max_json_bytes=_positive_limit('AGENT_REQUEST_MAX_JSON_BYTES', DEFAULT_MAX_JSON_BYTES),
        )

    @property
    def preparation_limit_chars(self) -> int:
        """Keep preparation within its target and never above the hard wire cap."""
        return min(self.max_preparation_chars, self.max_json_chars)


class RequestBudgetManager:
    """Measure and enforce character and UTF-8 byte limits on final wire JSON."""

    def __init__(self, limits: RequestBudgetLimits | None = None):
        self.limits = limits or RequestBudgetLimits.from_environment()

    @staticmethod
    def measure(wire_body: str | bytes) -> dict[str, int]:
        if isinstance(wire_body, bytes):
            try:
                serialized = wire_body.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise RequestBudgetExceeded(
                    {
                        'code': 'request_json_invalid_utf8',
                        'message': 'The serialized model request is not valid UTF-8.',
                    }
                ) from exc
            serialized_bytes = len(wire_body)
        else:
            serialized = wire_body
            serialized_bytes = len(wire_body.encode('utf-8'))
        return {
            'serialized_chars': len(serialized),
            'serialized_bytes': serialized_bytes,
        }

    def validate(self, wire_body: str | bytes) -> dict[str, int]:
        metrics = self.measure(wire_body)
        if metrics['serialized_chars'] > self.limits.max_json_chars:
            raise RequestBudgetExceeded(
                {
                    'code': 'request_json_char_limit_exceeded',
                    'message': 'The serialized model request exceeds the configured character limit.',
                    'limit_chars': self.limits.max_json_chars,
                    'limit_bytes': self.limits.max_json_bytes,
                    **metrics,
                }
            )
        if metrics['serialized_bytes'] > self.limits.max_json_bytes:
            raise RequestBudgetExceeded(
                {
                    'code': 'request_json_byte_limit_exceeded',
                    'message': 'The serialized model request exceeds the configured UTF-8 byte limit.',
                    'limit_chars': self.limits.max_json_chars,
                    'limit_bytes': self.limits.max_json_bytes,
                    **metrics,
                }
            )
        return metrics
