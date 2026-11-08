"""Extracted tool results adapter; legacy imports are re-exported by middleware."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from typing import Any

from fastapi.responses import HTMLResponse

from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.mcp.apps import MCPAppResult

log = logging.getLogger(__name__)


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

    if parsed.get('code') in {
        'tool_result_too_large',
        'resource_read_limit',
        'resource_task_time_limit',
    }:
        return True

    if _has_error_payload(parsed.get('error')):
        return True

    status = parsed.get('status')
    if isinstance(status, str) and status.strip().lower() in {'error', 'failed'}:
        return True

    if parsed.get('success') is False or parsed.get('ok') is False:
        return _has_error_payload(parsed.get('message'))

    return False


def _has_error_payload(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (dict, list)) and bool(value)

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


BASE64_IMAGE_DATA_URI_RE = re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+={0,2}', re.IGNORECASE)


def extract_base64_images(value: Any, files: list) -> Any:
    """Move base64 image data URIs out of a tool result so they do not reach the model as text."""
    if isinstance(value, str):
        if BASE64_IMAGE_DATA_URI_RE.fullmatch(value):
            files.append({'type': 'image', 'url': value})
            return '[image]'
        return value
    if isinstance(value, dict):
        return {key: extract_base64_images(item, files) for key, item in value.items()}
    if isinstance(value, list):
        return [extract_base64_images(item, files) for item in value]
    if isinstance(value, tuple):
        return tuple(extract_base64_images(item, files) for item in value)
    return value


def _ui_component_result(tool_name: str, message: str) -> dict:
    return {'status': 'success', 'code': 'ui_component', 'message': f'{tool_name}: {message}'}


def _html_response_result(tool_name: str, tool_result: Any) -> tuple[Any, list]:
    result_context = None
    if isinstance(tool_result, tuple) and len(tool_result) == 2 and isinstance(tool_result[0], HTMLResponse):
        tool_result, result_context = tool_result
    if not isinstance(tool_result, HTMLResponse):
        return tool_result, []

    if 'inline' not in tool_result.headers.get('Content-Disposition', ''):
        return tool_result.body.decode('utf-8', 'replace'), []

    embed = tool_result.body.decode('utf-8', 'replace')
    status_code = tool_result.status_code
    if 200 <= status_code < 300:
        if isinstance(result_context, (str, dict, list)):
            return result_context, [embed]
        return _ui_component_result(tool_name, 'Embedded UI result is active and visible to the user.'), [embed]
    if 400 <= status_code < 500:
        message = f'Client error {status_code} from embedded UI result.'
    elif 500 <= status_code < 600:
        message = f'Server error {status_code} from embedded UI result.'
    else:
        message = f'Unexpected status code {status_code} from embedded UI result.'
    result = {'status': 'error', 'code': 'ui_component', 'message': f'{tool_name}: {message}'}
    return result, [embed]


def _external_response_result(
    tool_name: str, tool_result: Any, tool_type: str, direct_tool: bool,
) -> tuple[Any, list]:
    external_tool_types = ('external', 'action', 'terminal')
    is_external_pair = tool_type in external_tool_types and isinstance(tool_result, tuple)
    is_direct_pair = direct_tool and isinstance(tool_result, list) and len(tool_result) == 2
    if not (is_external_pair or is_direct_pair):
        return tool_result, []

    tool_result, headers = tool_result
    try:
        if not isinstance(headers, dict):
            headers = dict(headers)
    except Exception as error:
        headers = {}
        log.debug(error)

    if not headers:
        return tool_result, []
    disposition = headers.get('Content-Disposition', headers.get('content-disposition', ''))
    if 'inline' not in disposition:
        return tool_result, []

    content_type = headers.get('Content-Type', headers.get('content-type', ''))
    location = headers.get('Location', headers.get('location', ''))
    handled, embed, result_context = _external_embed_context(content_type, location, tool_result)
    if not handled:
        return tool_result, []
    if isinstance(result_context, (str, dict, list)):
        return result_context, [embed]
    return _ui_component_result(tool_name, 'Embedded UI result is active and visible to the user.'), [embed]


def _external_embed_context(content_type: str, location: str, tool_result: Any) -> tuple[bool, Any, Any]:
    result_context = None
    embed = None
    if 'text/html' in content_type:
        embed = tool_result
        if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
            embed, result_context = tool_result
    elif location:
        embed = location
        if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
            _, result_context = tool_result
    else:
        return False, None, None
    return True, embed, result_context


def _parse_mcp_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return JSONCodec.loads(value)
        except JSONCodec.JSONDecodeError:
            pass
    return value


def _mcp_resource_content(resource: dict, files: list) -> tuple[bool, Any]:
    value = resource.get('text', '')
    if isinstance(value, str) and value:
        return True, _parse_mcp_text(value)
    if resource.get('blob'):
        mime_type = resource.get('mimeType') or 'application/octet-stream'
        if mime_type.startswith('image/'):
            files.append({'type': 'image', 'url': f"data:{mime_type};base64,{resource['blob']}"})
            return False, None
        uri = resource.get('uri', 'resource')
        return True, f'[Resource: {uri}] (binary data, mimeType: {mime_type})'
    if resource.get('uri'):
        return True, resource['uri']
    return False, None


async def _mcp_binary_file(request, item: dict, metadata: dict | None, user: Any) -> dict:
    from open_webui.utils.files import get_file_url_from_base64

    file_url = await get_file_url_from_base64(
        request,
        f"data:{item.get('mimeType')};base64,{item.get('data', item.get('blob', ''))}",
        {
            'chat_id': metadata.get('chat_id') if metadata else None,
            'message_id': metadata.get('message_id') if metadata else None,
            'session_id': metadata.get('session_id') if metadata else None,
        },
        user,
    )
    return {'type': item.get('type', 'data'), 'url': file_url}


async def _mcp_result_content(request, tool_result: list, metadata: dict | None, user: Any, files: list) -> Any:
    tool_response = []
    for item in tool_result:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type == 'text':
            tool_response.append(_parse_mcp_text(item.get('text', '')))
        elif item_type in {'image', 'audio'}:
            files.append(await _mcp_binary_file(request, item, metadata, user))
        elif item_type == 'resource':
            include, value = _mcp_resource_content(item.get('resource', {}), files)
            if include:
                tool_response.append(value)
    return tool_response[0] if len(tool_response) == 1 else tool_response


def _extract_openapi_data_files(tool_result: list, files: list) -> None:
    # Keep image data URIs for the image extractor; move other data URIs to attachments.
    for item in list(tool_result):
        if isinstance(item, str) and item.startswith('data:') and not BASE64_IMAGE_DATA_URI_RE.fullmatch(item):
            files.append({'type': 'data', 'content': item})
            tool_result.remove(item)


async def process_tool_result(
    request,
    tool_function_name,
    tool_result,
    tool_type,
    direct_tool=False,
    metadata=None,
    user=None,
):
    tool_result_embeds = []
    if isinstance(tool_result, MCPAppResult):
        tool_result_embeds.append(tool_result.embed)
        tool_result = tool_result.context

    tool_result, embeds = _html_response_result(tool_function_name, tool_result)
    tool_result_embeds.extend(embeds)
    tool_result, embeds = _external_response_result(tool_function_name, tool_result, tool_type, direct_tool)
    tool_result_embeds.extend(embeds)

    tool_result_files = []
    if isinstance(tool_result, str) and tool_result.startswith('data:image/'):
        tool_result_files.append({'type': 'image', 'url': tool_result})
        tool_result = f'{tool_function_name}: Image file read successfully.'

    if isinstance(tool_result, list):
        if tool_type == 'mcp':
            tool_result = await _mcp_result_content(request, tool_result, metadata, user, tool_result_files)
        else:
            _extract_openapi_data_files(tool_result, tool_result_files)

    tool_result = extract_base64_images(tool_result, tool_result_files)
    if isinstance(tool_result, list):
        tool_result = {'results': tool_result}
    if isinstance(tool_result, (dict, list)):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

    # Keep the result contract string-safe for downstream concatenation.
    if tool_result is not None and not isinstance(tool_result, str):
        if isinstance(tool_result, tuple):
            tool_result = json.dumps(tool_result[0], indent=2, ensure_ascii=False) if tool_result else ''
        else:
            tool_result = str(tool_result)

    return tool_result, tool_result_files, tool_result_embeds
