"""Extracted messages adapter; legacy imports are re-exported by middleware."""

from __future__ import annotations

from open_webui.utils.misc import convert_output_to_messages, get_paired_tool_call_ids, merge_system_messages, strip_empty_content_blocks


def normalize_messages_for_model(form_data: dict) -> dict:
    form_data['messages'] = strip_empty_content_blocks(form_data.get('messages', []))
    form_data['messages'] = merge_system_messages(form_data.get('messages', []))
    return form_data

def get_reasoning_format(model: dict) -> str | None:
    """
    Determine how reasoning should be included in reconstructed messages.

    Returns:
        'thinking': Ollama expects reasoning in the native thinking field.
        'think_tags': wrap reasoning in <think> tags inside content.
        'reasoning_content': llama.cpp supports reasoning_content as a top-level field.
        None: skip reasoning (safe default for strict providers).
    """
    provider = model.get('provider', '')
    if model.get('owned_by') == 'ollama':
        return 'thinking'
    if provider == 'llama.cpp':
        return 'reasoning_content'
    return None

def strip_reasoning_details(output: list) -> list:
    return [
        {key: value for key, value in item.items() if key != 'reasoning_details'} if isinstance(item, dict) else item
        for item in output
    ]

def strip_tool_images(output: list, model: dict) -> list:
    """Replace tool result images with a text note when the model has Vision turned off."""
    if (model.get('info', {}).get('meta', {}).get('capabilities') or {}).get('vision', True):
        return output
    stripped_output = []
    for item in output:
        if isinstance(item, dict) and item.get('type') == 'function_call_output':
            parts = [
                {'type': 'input_text', 'text': '\n[image]'} if part.get('type') == 'input_image' else part
                for part in item.get('output', [])
            ]
            item = {**item, 'output': parts}
        stripped_output.append(item)
    return stripped_output


def add_file_context(messages: list[dict]) -> list[dict]:
    """Add safe file references to user messages for native tool-calling models."""
    def format_file_tag(file: dict) -> str:
        attrs = f'type="{file.get("type", "file")}" id="{file.get("id") or file.get("url")}"'
        if file.get('url'):
            attrs += f' url="{file["url"]}"'
        if file.get('content_type'):
            attrs += f' content_type="{file["content_type"]}"'
        if file.get('name'):
            attrs += f' name="{file["name"]}"'
        return f'<file {attrs}/>'

    for message in messages:
        if message.get('role') != 'user':
            continue
        attached_files = [
            file
            for file in message.get('files', [])
            if (file.get('url') and not file.get('url').startswith('data:'))
            or (file.get('type') == 'chat' and file.get('id'))
        ]
        if not attached_files:
            continue

        file_context = '<attached_files>\n' + '\n'.join(map(format_file_tag, attached_files)) + '\n</attached_files>\n\n'
        content = message.get('content', '')
        if isinstance(content, list):
            message['content'] = [{'type': 'text', 'text': file_context}] + content
        else:
            message['content'] = file_context + content

    return messages


def process_messages_with_output(
    messages: list[dict],
    reasoning_format: str | None = None,
    include_file_context: bool = False,
) -> list[dict]:
    """
    Process messages with OR-aligned output items for LLM consumption.

    For assistant messages with 'output' field, produces properly formatted
    OpenAI-style messages (tool_calls + tool results). Strips 'output' before LLM.
    """
    processed = []

    for message in messages:
        meta = message.get('meta')
        voice = meta.get('voice') if isinstance(meta, dict) else None
        voice = voice if isinstance(voice, dict) else {}
        spoken = (
            message.get('role') == 'assistant'
            and voice
            and message.get('model') == voice.get('model')
            and not message.get('output')
        )
        # Keep voice speech available for follow-ups without attributing its status claims
        # to the reasoning model. A completed chat answer can predate a stale spoken reply.
        transcripts = []
        if message.get('role') == 'assistant':
            transcripts = voice.get('speech') or []
        if spoken:
            transcripts = [{'transcript': message.get('content', '')}]
        speech = [
            {
                'role': 'assistant',
                'content': '[Historical voice assistant transcript; not current task status]\n' + item['transcript'],
            }
            for item in transcripts
            if isinstance(item, dict) and isinstance(item.get('transcript'), str) and item['transcript']
        ]
        if spoken:
            processed.extend(speech)
            continue
        if message.get('role') == 'assistant' and message.get('output'):
            # Use output items for clean OpenAI-format messages
            output_messages = convert_output_to_messages(
                message['output'],
                raw=True,
                reasoning_format=reasoning_format,
                flatten_tool_images=True,
            )
            if output_messages:
                processed.extend(output_messages)
                processed.extend(speech)
                continue
            if not message.get('content'):
                continue

        clean_message = dict(message)
        for key in ('id', 'output', 'model', 'contextSummary', 'context_summary', 'usage', 'meta'):
            clean_message.pop(key, None)
        processed.append(clean_message)
        processed.extend(speech)

    if include_file_context:
        add_file_context(processed)
    for message in processed:
        message.pop('files', None)

    return processed

def sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    paired_ids_by_message = get_paired_tool_call_ids(messages)

    sanitized = []
    for message, paired_ids in zip(messages, paired_ids_by_message):
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            kept = [tool_call for tool_call in message.get('tool_calls') or [] if tool_call.get('id') in paired_ids]
            if kept:
                sanitized.append({**message, 'tool_calls': kept})
            else:
                clean = dict(message)
                clean.pop('tool_calls', None)
                clean.pop('reasoning_items', None)
                if clean.get('content'):
                    sanitized.append(clean)
        elif message.get('role') != 'tool' or message.get('tool_call_id') in paired_ids:
            sanitized.append(message)

    return sanitized
