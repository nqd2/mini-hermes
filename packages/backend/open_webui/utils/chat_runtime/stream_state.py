"""Extracted stream state adapter; legacy imports are re-exported by middleware."""

from __future__ import annotations

import copy
import json
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import JSONResponse

from open_webui.env import ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.response import handle_responses_streaming_event, update_usage_snapshot


def _start_tag_pattern(start_tag: str) -> str:
    if start_tag.startswith('<') and start_tag.endswith('>'):
        return rf'<{re.escape(start_tag[1:-1])}(\s.*?)?>'
    return re.escape(start_tag)


def output_id(prefix: str) -> str:
    """Generate OR-style ID: prefix + 24-char hex UUID."""
    return f'{prefix}_{uuid4().hex[:24]}'


def combine_stream_outputs(continuing: bool, prior_output: list, output: list) -> list:
    """Merge the first assistant message across a continuation response."""
    if (
        continuing
        and prior_output
        and output
        and prior_output[-1].get('type') == output[0].get('type') == 'message'
        and prior_output[-1].get('role', 'assistant') == 'assistant'
        and output[0].get('role', 'assistant') == 'assistant'
    ):
        return [
            *prior_output[:-1],
            {
                **prior_output[-1],
                **output[0],
                'id': prior_output[-1].get('id'),
                'content': [*prior_output[-1].get('content', []), *output[0].get('content', [])],
            },
            *output[1:],
        ]
    return prior_output + output if prior_output else output


def get_stream_message_error_content(error):
    """Convert provider/route errors to the stable chat error payload shape."""
    if isinstance(error, HTTPException):
        error = error.detail
    elif isinstance(error, dict):
        error = error.get('detail', error)
    else:
        error = str(error)

    return error if isinstance(error, (str, dict)) else str(error)


def mark_stream_output_incomplete(output: list) -> list:
    """Mark unfinished stream items and tool calls without results as incomplete."""
    result_call_ids = {
        item.get('call_id') for item in output if item.get('type') == 'function_call_output' and item.get('call_id')
    }
    for item in output:
        is_running_tool_call = (
            item.get('type') == 'function_call'
            and item.get('status') == 'completed'
            and (item.get('call_id') or item.get('id')) not in result_call_ids
        )
        if item.get('status') == 'in_progress' or is_running_tool_call:
            item['status'] = 'incomplete'
    return output


def append_to_text_field(item: dict, key: str, value: str) -> None:
    # Default: keep the existing field intact until concatenation succeeds.
    # Non-string values and str subclasses also use this path to preserve errors.
    if not ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND or type(item[key]) is not str or type(value) is not str:
        item[key] += value
        return

    # Opt-in: dropping the dict's reference lets CPython extend an unshared str
    # in place. Only a host that is already out of memory can leave the field empty.
    text = item[key]
    item[key] = ''
    text += value
    item[key] = text


def merge_streamed_reasoning_details(target: list, details) -> None:
    items = details if isinstance(details, list) else [details]
    for item in items:
        if not isinstance(item, dict):
            continue

        index = item.get('index')
        existing = (
            next((detail for detail in target if detail.get('index') == index), None)
            if isinstance(index, int)
            else None
        )
        if existing is None:
            target.append(dict(item))
            continue

        for key, value in item.items():
            if key in ('text', 'summary') and isinstance(value, str) and isinstance(existing.get(key), str):
                append_to_text_field(existing, key, value)
            else:
                existing[key] = value


def _split_tool_calls(
    tool_calls: list[dict],
) -> list[dict]:
    """Expand tool calls whose arguments contain multiple back-to-back JSON objects.

    Some models (e.g. GPT-5.4) send multiple complete JSON argument objects
    under the same tool call index, producing concatenated invalid JSON like:
        '{"query":"A","count":5}{"query":"B","count":5}'

    Each such tool call is split into separate entries so each gets executed
    independently. Single-object arguments pass through unchanged.
    """

    def split_json_objects(raw: str) -> list[str]:
        if not isinstance(raw, str):
            raw = '' if raw is None else JSONCodec.dumps(raw)

        decoder = json.JSONDecoder()
        results = []
        position = 0

        while position < len(raw):
            while position < len(raw) and raw[position].isspace():
                position += 1
            if position >= len(raw):
                break
            try:
                _, end = decoder.raw_decode(raw, position)
                results.append(raw[position:end].strip())
                position = end
            except JSONCodec.JSONDecodeError:
                return [raw]

        return results or [raw]

    expanded = []
    for tool_call in tool_calls:
        function = tool_call.setdefault('function', {})
        arguments = function.get('arguments')
        if not isinstance(arguments, str):
            arguments = '' if arguments is None else JSONCodec.dumps(arguments)
            function['arguments'] = arguments
        split_arguments = split_json_objects(arguments)

        if len(split_arguments) <= 1:
            expanded.append(tool_call)
        else:
            for argument in split_arguments:
                cloned = copy.deepcopy(tool_call)
                cloned['id'] = f'call_{uuid4().hex[:24]}'
                cloned['function']['arguments'] = argument
                expanded.append(cloned)

    return expanded


RESPONSE_COMPLETION_RESPONSE_FIELDS = ('error', 'id', 'output', 'usage')


def get_response_completion_event_data(event: dict) -> dict:
    """Build the data payload for response:completion events."""
    response = event.get('response')
    if not isinstance(response, dict):
        return event

    response_data = {key: response[key] for key in RESPONSE_COMPLETION_RESPONSE_FIELDS if key in response}

    return {
        **event,
        'response': response_data,
    }


def get_response_data(response):
    if isinstance(response, list) and len(response) == 1:
        # If the response is a single-item list, unwrap it #17213
        response = response[0]

    if isinstance(response, JSONResponse):
        if isinstance(response.body, bytes):
            try:
                response_data = JSONCodec.loads(response.body.decode('utf-8', 'replace'))
            except JSONCodec.JSONDecodeError:
                response_data = {'error': {'detail': 'Invalid JSON response'}}
        else:
            response_data = response
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = None

    return response, response_data


def merge_events_into_response(response_data, events):
    if events and isinstance(events, list):
        extra_response = {}
        for event in events:
            if isinstance(event, dict):
                extra_response.update(event)
            else:
                extra_response[event] = True

        return {
            **extra_response,
            **response_data,
        }
    return response_data


def build_response_object(response, response_data):
    if isinstance(response, dict):
        return response_data
    if isinstance(response, JSONResponse):
        return JSONResponse(
            content=response_data,
            headers=response.headers,
            status_code=response.status_code,
        )
    return response


def update_assistant_message_from_stream(assistant_message, raw) -> bool:
    """Accumulate the message and report whether the chunk contains [DONE]."""
    line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
    if not isinstance(line, str):
        return False

    def append_output_text(item, text):
        parts = item.setdefault('content', [])
        if parts and parts[-1].get('type') == 'output_text':
            append_to_text_field(parts[-1], 'text', text)
        else:
            parts.append({'type': 'output_text', 'text': text})

    done = False
    for raw_part in line.splitlines():
        part = raw_part.removeprefix('data:').strip()
        if part == '[DONE]':
            done = True
            continue
        if not part:
            continue

        try:
            data = JSONCodec.loads(part)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        if data.get('type', '').startswith('response.'):
            output, meta = handle_responses_streaming_event(data, assistant_message.get('output', []))
            if output:
                assistant_message['output'] = output
            if meta and meta.get('usage'):
                assistant_message['usage'] = update_usage_snapshot(assistant_message.get('usage'), meta['usage'])
            continue

        raw_usage = data.get('usage', {}) or {}
        raw_usage.update(data.get('timings', {}))
        if raw_usage:
            assistant_message['usage'] = update_usage_snapshot(assistant_message.get('usage'), raw_usage)

        for choice in data.get('choices', []):
            if choice.get('finish_reason'):
                assistant_message['finish_reason'] = choice['finish_reason']
            delta = choice.get('delta', {}) or {}
            content = delta.get('content')
            reasoning_content = delta.get('reasoning_content') or delta.get('reasoning') or delta.get('thinking')

            if reasoning_content:
                output = assistant_message.setdefault('output', [])
                if not output or output[-1].get('type') != 'reasoning':
                    output.append(
                        {
                            'type': 'reasoning',
                            'id': output_id('r'),
                            'status': 'in_progress',
                            'start_tag': '<think>',
                            'end_tag': '</think>',
                            'attributes': {'type': 'reasoning_content'},
                            'content': [],
                            'summary': None,
                            'started_at': time.time(),
                        }
                    )

                append_output_text(output[-1], reasoning_content)

            if content:
                output = assistant_message.get('output')
                if output:
                    if output[-1].get('type') == 'reasoning':
                        output[-1]['status'] = 'completed'
                        output[-1]['ended_at'] = time.time()
                        output[-1]['duration'] = int(output[-1]['ended_at'] - output[-1]['started_at'])

                    if not output or output[-1].get('type') != 'message':
                        output.append(
                            {
                                'type': 'message',
                                'id': output_id('msg'),
                                'status': 'in_progress',
                                'role': 'assistant',
                                'content': [],
                            }
                        )

                    append_output_text(output[-1], content)

                if 'content' in assistant_message:
                    append_to_text_field(assistant_message, 'content', content)
                else:
                    assistant_message['content'] = '' + content

    return done
