"""Extracted tool results adapter; legacy imports are re-exported by middleware."""

from __future__ import annotations

from open_webui.utils.json_codec import JSONCodec
from typing import Any
import mimetypes
import os


def _is_tool_result_error(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if (
            text.startswith('error:')
            or text.startswith('exception:')
            or text.startswith('traceback')
            or text.startswith('http error!')
        ):
            return True

    parsed = value
    while isinstance(parsed, str):
        try:
            parsed = JSONCodec.loads(parsed)
        except (JSONCodec.JSONDecodeError, TypeError, ValueError):
            break

    if not isinstance(parsed, dict):
        return False

    error = parsed.get('error')
    if isinstance(error, str):
        has_error = bool(error.strip())
    else:
        has_error = isinstance(error, (dict, list)) and bool(error)
    if has_error:
        return True

    status = parsed.get('status')
    if isinstance(status, str) and status.strip().lower() in {'error', 'failed'}:
        return True

    if parsed.get('success') is False or parsed.get('ok') is False:
        message = parsed.get('message')
        return has_error or (
            bool(message.strip()) if isinstance(message, str) else isinstance(message, (dict, list)) and bool(message)
        )

    return False

def build_terminal_file_tool_result(
    tool_function_name: str,
    tool_function_params: dict,
    tool_result: Any,
    tool: dict | None,
    metadata: dict | None,
) -> dict | None:
    if isinstance(tool_result, (list, tuple)) and tool_result and isinstance(tool_result[0], dict):
        tool_result = tool_result[0]

    if tool_function_name != 'display_file' or not isinstance(tool_result, dict) or tool_result.get('exists') is False:
        return None

    tool_id = (tool or {}).get('tool_id', '')
    terminal_id = metadata.get('terminal_id') if metadata else None
    if isinstance(tool_id, str) and tool_id.startswith('terminal:'):
        terminal_id = tool_id.split(':', 1)[1]

    server_url = ((tool or {}).get('server') or {}).get('url')
    terminal_selector = terminal_id or server_url
    path = tool_result.get('path') or tool_function_params.get('path')
    if not terminal_selector or not path:
        return None
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or 'application/octet-stream'
    page = tool_result.get('page') or tool_function_params.get('page')

    return {
        **tool_result,
        'type': 'file',
        'source': 'open_terminal',
        'exists': True,
        'displayed': tool_function_params.get('inline') is True,
        'terminal_selector': terminal_selector,
        **({'terminal_id': terminal_id} if terminal_id else {}),
        **({'terminal_url': server_url} if server_url and not terminal_id else {}),
        'session_id': metadata.get('chat_id') if metadata else None,
        'path': path,
        'full_path': tool_result.get('full_path') or path,
        'name': tool_result.get('name') or os.path.basename(path),
        'mime_type': tool_result.get('mime_type') or tool_result.get('content_type') or mime_type,
        'content_type': tool_result.get('content_type') or tool_result.get('mime_type') or mime_type,
        **({'page': page} if page else {}),
    }

def tool_result_content(tool_result: Any) -> str:
    if not tool_result:
        return ''
    if isinstance(tool_result, (dict, list)):
        return JSONCodec.dumps(tool_result, ensure_ascii=False)
    return str(tool_result)

def parse_terminal_tool_result(tool_result: Any) -> dict:
    try:
        result = JSONCodec.loads(tool_result)
    except (JSONCodec.JSONDecodeError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}
