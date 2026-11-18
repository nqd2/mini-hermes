"""Development-only JSON wire logging for frontend HTTP and upstream LLM API traffic."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from typing import Any
from weakref import WeakKeyDictionary

from weai.utils.json_codec import JSONCodec
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})
_SENSITIVE_KEYS = frozenset(
    {
        'password',
        'new_password',
        'current_password',
        'api_key',
        'secret',
        'secret_key',
        'access_token',
        'refresh_token',
    }
)
_WIRE_META: WeakKeyDictionary[Any, tuple[str, str]] = WeakKeyDictionary()
_FILE_LOCK = threading.Lock()


def is_dev_json_wire_log_enabled() -> bool:
    """Return True when dev JSON wire logging is enabled via environment variable."""
    return os.getenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG', '').strip().lower() in _TRUTHY


def _max_wire_chars() -> int:
    raw = os.getenv('AGENT_RUNTIME_DEV_JSON_WIRE_MAX_CHARS', '120000').strip()
    try:
        value = int(raw)
        return max(value, 0)
    except ValueError:
        return 120000


def _sanitize_value(value: Any, *, redact_token: bool = False) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SENSITIVE_KEYS or (redact_token and lowered == 'token'):
                sanitized[key] = '***REDACTED***'
            else:
                sanitized[key] = _sanitize_value(item, redact_token=redact_token)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item, redact_token=redact_token) for item in value]
    return value


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f'{text[:max_chars]}...<truncated {omitted} chars>'


def format_json_payload(data: Any, *, redact_token: bool = False) -> str:
    """Parse bytes/str/object as JSON (if valid), redact sensitive keys, and compact-serialize."""
    max_chars = _max_wire_chars()
    parsed = data
    if isinstance(data, (bytes, bytearray)):
        if not data:
            return ''
        raw_text = bytes(data).decode('utf-8', errors='replace')
        try:
            parsed = JSONCodec.loads(raw_text)
        except Exception:
            return _truncate_text(raw_text, max_chars)
    elif isinstance(data, str):
        if not data:
            return ''
        try:
            parsed = JSONCodec.loads(data)
        except Exception:
            return _truncate_text(data, max_chars)

    if isinstance(parsed, (dict, list)):
        cleaned = _sanitize_value(parsed, redact_token=redact_token)
        serialized = json.dumps(cleaned, ensure_ascii=False, separators=(',', ':'), default=str)
    else:
        serialized = json.dumps(parsed, ensure_ascii=False, default=str)
    return _truncate_text(serialized, max_chars)


def _emit_wire_record(tag: str, header: str, body_text: str, *, event_meta: dict[str, Any]) -> None:
    log.info('[DEV_JSON_WIRE][%s] %s body=%s', tag, header, body_text)
    log_file = os.getenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG_FILE', '').strip()
    if not log_file:
        return
    record = {'tag': tag, **event_meta, 'body': body_text}
    with contextlib.suppress(Exception), _FILE_LOCK:
        parent = os.path.dirname(log_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(log_file, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def _should_redact_token(path: str) -> bool:
    lowered = path.lower()
    return '/auths' in lowered or '/configs' in lowered


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(';', 1)[0].strip().lower()
    return media_type == 'application/json' or media_type.endswith('+json')


def _is_sse_content_type(content_type: str) -> bool:
    return content_type.split(';', 1)[0].strip().lower() == 'text/event-stream'


def _should_log_http_path(path: str) -> bool:
    return path.startswith(('/api/', '/openai/', '/v1/'))


class SSEWireTap:
    """Incrementally collects SSE `data:` JSON frames and emits one assembled JSON log."""

    def __init__(self, *, tag: str, header: str, event_meta: dict[str, Any], redact_token: bool = False) -> None:
        self.tag = tag
        self.header = header
        self.event_meta = event_meta
        self.redact_token = redact_token
        self._buffer = bytearray()
        self._chunk_count = 0
        self._stream_id: Any = None
        self._model: Any = None
        self._role: str | None = None
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._finish_reasons: list[str] = []
        self._usage: Any = None
        self._error: Any = None
        self._extra_events: list[dict[str, Any]] = []
        self._finalized = False

    def feed(self, chunk: bytes | str) -> None:
        if self._finalized or not chunk:
            return
        raw = chunk.encode('utf-8', errors='replace') if isinstance(chunk, str) else bytes(chunk)
        self._buffer.extend(raw)
        while b'\n' in self._buffer:
            line_bytes, _, rest = self._buffer.partition(b'\n')
            self._buffer = bytearray(rest)
            self._process_line(line_bytes.decode('utf-8', errors='replace').strip())

    def _process_line(self, line: str) -> None:
        if not line.startswith('data:'):
            return
        payload_str = line[5:].strip()
        if not payload_str or payload_str == '[DONE]':
            return
        try:
            event = JSONCodec.loads(payload_str)
        except Exception:
            return
        if not isinstance(event, dict):
            return

        self._chunk_count += 1
        if self._stream_id is None and event.get('id') is not None:
            self._stream_id = event.get('id')
        if self._model is None and event.get('model') is not None:
            self._model = event.get('model')
        if event.get('usage') is not None:
            self._usage = event.get('usage')
        if event.get('error') is not None:
            self._error = event.get('error')

        choices = event.get('choices')
        if isinstance(choices, list) and choices:
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                if choice.get('finish_reason'):
                    self._finish_reasons.append(str(choice['finish_reason']))
                delta = choice.get('delta')
                if isinstance(delta, dict):
                    self._consume_delta(delta)
                msg = choice.get('message')
                if isinstance(msg, dict):
                    self._consume_delta(msg)
            return

        if len(self._extra_events) < 50:
            self._extra_events.append(event)

    def _consume_delta(self, delta: dict[str, Any]) -> None:
        if delta.get('role') and self._role is None:
            self._role = str(delta['role'])
        if delta.get('content'):
            self._content_parts.append(str(delta['content']))
        if delta.get('reasoning_content'):
            self._reasoning_parts.append(str(delta['reasoning_content']))
        if delta.get('reasoning'):
            self._reasoning_parts.append(str(delta['reasoning']))
        for tc in delta.get('tool_calls') or []:
            if not isinstance(tc, dict):
                continue
            idx = int(tc.get('index', 0))
            slot = self._tool_calls.setdefault(
                idx,
                {'index': idx, 'id': None, 'type': tc.get('type') or 'function', 'function': {'name': '', 'arguments': ''}},
            )
            if tc.get('id'):
                slot['id'] = tc['id']
            if tc.get('type'):
                slot['type'] = tc['type']
            fn = tc.get('function')
            if isinstance(fn, dict):
                if fn.get('name'):
                    slot['function']['name'] += str(fn['name'])
                if fn.get('arguments'):
                    slot['function']['arguments'] += str(fn['arguments'])

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._buffer:
            self._process_line(bytes(self._buffer).decode('utf-8', errors='replace').strip())
            self._buffer.clear()

        summary: dict[str, Any] = {'chunk_count': self._chunk_count}
        if self._stream_id is not None:
            summary['id'] = self._stream_id
        if self._model is not None:
            summary['model'] = self._model
        if self._finish_reasons:
            summary['finish_reasons'] = self._finish_reasons

        message: dict[str, Any] = {}
        if self._role is not None:
            message['role'] = self._role
        if self._reasoning_parts:
            message['reasoning_content'] = ''.join(self._reasoning_parts)
        if self._content_parts:
            message['content'] = ''.join(self._content_parts)
        if self._tool_calls:
            message['tool_calls'] = [self._tool_calls[idx] for idx in sorted(self._tool_calls)]
        if message:
            summary['message'] = message
        if self._usage is not None:
            summary['usage'] = self._usage
        if self._error is not None:
            summary['error'] = self._error
        if self._extra_events:
            summary['events'] = self._extra_events

        body_text = format_json_payload(summary, redact_token=self.redact_token)
        _emit_wire_record(self.tag, self.header, body_text, event_meta=self.event_meta)


def log_llm_request(method: str, url: str, wire_body: str | bytes, response_target: Any = None) -> str:
    """Log an outbound JSON request sent from backend to an LLM API and return a req_id."""
    req_id = uuid.uuid4().hex[:8]
    if response_target is not None:
        attach_dev_wire_meta(response_target, req_id, url)
    if not is_dev_json_wire_log_enabled():
        return req_id
    body_text = format_json_payload(wire_body)
    _emit_wire_record(
        'BE->LLM',
        f'req_id={req_id} {method.upper()} {url}',
        body_text,
        event_meta={'req_id': req_id, 'method': method.upper(), 'url': url},
    )
    return req_id


def attach_dev_wire_meta(response: Any, req_id: str, url: str) -> None:
    if response is None:
        return
    with contextlib.suppress(TypeError):
        _WIRE_META[response] = (req_id, url)


def _get_wire_meta(response: Any, fallback_url: str = '') -> tuple[str, str]:
    if response is not None:
        with contextlib.suppress(TypeError):
            meta = _WIRE_META.get(response)
            if meta is not None:
                return meta
    return ('-', fallback_url)


def log_llm_json_response(response: Any, url: str, data: Any) -> None:
    """Log a non-streaming JSON or error response received by backend from an LLM API."""
    if not is_dev_json_wire_log_enabled():
        return
    req_id, stored_url = _get_wire_meta(response, url)
    effective_url = stored_url or url
    status_code = getattr(response, 'status', None)
    if status_code is None:
        status_code = getattr(response, 'status_code', '-')
    body_text = format_json_payload(data)
    _emit_wire_record(
        'LLM->BE',
        f'req_id={req_id} status={status_code} {effective_url}',
        body_text,
        event_meta={'req_id': req_id, 'status': status_code, 'url': effective_url},
    )


def create_sse_wire_tap(response: Any, *, url: str = '') -> SSEWireTap | None:
    """Create an SSEWireTap for an upstream streaming response when dev wire logging is active."""
    if not is_dev_json_wire_log_enabled():
        return None
    req_id, stored_url = _get_wire_meta(response, url)
    effective_url = stored_url or url or str(getattr(response, 'url', '') or '')
    status_code = getattr(response, 'status', None)
    if status_code is None:
        status_code = getattr(response, 'status_code', '-')
    return SSEWireTap(
        tag='LLM->BE:SSE',
        header=f'req_id={req_id} status={status_code} {effective_url}',
        event_meta={'req_id': req_id, 'status': status_code, 'url': effective_url},
    )


class DevJsonWireMiddleware:
    """Pure-ASGI middleware logging frontend JSON requests and JSON/SSE responses in dev."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not is_dev_json_wire_log_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')
        if not _should_log_http_path(path):
            await self.app(scope, receive, send)
            return

        method = scope.get('method', 'GET').upper()
        query = scope.get('query_string', b'').decode('latin-1', errors='replace')
        full_path = f'{path}?{query}' if query else path
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        req_is_json = _is_json_content_type(headers.get('content-type', ''))
        redact_token = _should_redact_token(path)

        req_buffer = bytearray()
        req_logged = False
        max_capture_bytes = max(_max_wire_chars() * 4, 262144)

        async def wrapped_receive() -> Message:
            nonlocal req_logged
            message = await receive()
            if req_is_json and message.get('type') == 'http.request':
                chunk = message.get('body', b'')
                if chunk and len(req_buffer) < max_capture_bytes:
                    req_buffer.extend(chunk[: max_capture_bytes - len(req_buffer)])
                if not message.get('more_body', False) and not req_logged:
                    req_logged = True
                    body_text = format_json_payload(bytes(req_buffer), redact_token=redact_token)
                    _emit_wire_record(
                        'FE->BE',
                        f'{method} {full_path}',
                        body_text,
                        event_meta={'method': method, 'path': full_path},
                    )
            return message

        resp_status: int | None = None
        resp_is_json = False
        resp_sse_tap: SSEWireTap | None = None
        resp_buffer = bytearray()

        async def wrapped_send(message: Message) -> None:
            nonlocal resp_status, resp_is_json, resp_sse_tap
            msg_type = message.get('type')
            if msg_type == 'http.response.start':
                resp_status = int(message.get('status', 0))
                resp_headers = {
                    k.decode('latin-1').lower(): v.decode('latin-1') for k, v in message.get('headers', [])
                }
                content_type = resp_headers.get('content-type', '')
                resp_is_json = _is_json_content_type(content_type)
                if _is_sse_content_type(content_type):
                    resp_sse_tap = SSEWireTap(
                        tag='BE->FE:SSE',
                        header=f'status={resp_status} {method} {full_path}',
                        event_meta={'status': resp_status, 'method': method, 'path': full_path},
                        redact_token=redact_token,
                    )
            elif msg_type == 'http.response.body':
                chunk = message.get('body', b'')
                more_body = bool(message.get('more_body', False))
                if resp_is_json:
                    if chunk and len(resp_buffer) < max_capture_bytes:
                        resp_buffer.extend(chunk[: max_capture_bytes - len(resp_buffer)])
                    if not more_body:
                        body_text = format_json_payload(bytes(resp_buffer), redact_token=redact_token)
                        _emit_wire_record(
                            'BE->FE',
                            f'status={resp_status} {method} {full_path}',
                            body_text,
                            event_meta={'status': resp_status, 'method': method, 'path': full_path},
                        )
                elif resp_sse_tap is not None:
                    if chunk:
                        resp_sse_tap.feed(chunk)
                    if not more_body:
                        resp_sse_tap.finalize()
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        finally:
            if resp_sse_tap is not None:
                resp_sse_tap.finalize()
