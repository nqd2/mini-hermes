"""Normalize provider replies into the assistant output structure used by chat."""

from typing import Any

from open_webui.utils.chat_runtime.stream_state import output_id
from open_webui.utils.misc import get_reasoning_details


def build_non_streaming_output(response_output: Any, choices: list[dict], content: str) -> Any:
    if response_output:
        return response_output

    choice_message = choices[0].get('message', {}) if choices else {}
    reasoning_content = choice_message.get('reasoning_content') or choice_message.get('reasoning')
    reasoning_details = get_reasoning_details(choice_message)
    response_output = []
    if reasoning_content or reasoning_details:
        reasoning_item = {
            'type': 'reasoning',
            'id': output_id('r'),
            'status': 'completed',
            'start_tag': '<think>',
            'end_tag': '</think>',
            'attributes': {'type': 'reasoning_content'},
            'content': [{'type': 'output_text', 'text': reasoning_content}] if reasoning_content else [],
            'summary': None,
        }
        if reasoning_details:
            reasoning_item['reasoning_details'] = (
                reasoning_details if isinstance(reasoning_details, list) else [reasoning_details]
            )
        response_output.append(reasoning_item)
    response_output.append(
        {
            'type': 'message',
            'id': output_id('msg'),
            'status': 'completed',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': content}],
        }
    )
    return response_output


def merge_continuing_output(assistant_message: dict, response_output: Any) -> list[dict]:
    previous = assistant_message.get('output') or []
    if not previous and assistant_message.get('content'):
        previous = [
            {
                'type': 'message',
                'id': assistant_message.get('id') or output_id('msg'),
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': assistant_message['content']}],
            }
        ]

    response_output = list(response_output)
    if (
        previous
        and response_output
        and previous[-1].get('type') == response_output[0].get('type') == 'message'
        and previous[-1].get('role', 'assistant') == 'assistant'
        and response_output[0].get('role', 'assistant') == 'assistant'
    ):
        response_output[0] = {
            **previous[-1],
            **response_output[0],
            'id': previous[-1].get('id'),
            'content': [*previous[-1].get('content', []), *response_output[0].get('content', [])],
        }
        previous = previous[:-1]

    return previous + response_output
