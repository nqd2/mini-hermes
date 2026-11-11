from __future__ import annotations

import pytest
from fastapi import HTTPException

from open_webui.agent_runtime.context import ContextManager
from open_webui.agent_runtime.request_budget import RequestBudgetLimits, RequestBudgetManager
from open_webui.utils.json_codec import JSONCodec


def _manager(chars: int = 1800) -> ContextManager:
    return ContextManager(
        request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_chars=chars, max_json_bytes=chars * 4)),
        compact_at_fraction=0.75,
    )


def test_context_compaction_drops_only_complete_turns_and_keeps_summary_and_latest_user():
    payload = {
        'model': 'small-test-model',
        'messages': [
            {'role': 'system', 'content': 'Keep this instruction.'},
            {'role': 'user', 'content': 'OLD-USER ' + 'a' * 900},
            {'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'type': 'function'}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'OLD-TOOL ' + 'b' * 500},
            {'role': 'assistant', 'content': 'OLD-ANSWER ' + 'c' * 250},
            {'role': 'user', 'content': 'LATEST-USER ' + 'd' * 120},
        ],
    }

    prepared = _manager().prepare(payload, 'https://provider.invalid/v1')

    assert len(JSONCodec.dumps(prepared.payload)) <= 1800
    assert prepared.metrics['compacted_messages'] == 4
    messages = prepared.payload['messages']
    assert messages[0]['content'] == 'Keep this instruction.'
    assert 'Earlier conversation summary' in messages[1]['content']
    assert 'OLD-USER' in messages[1]['content']
    assert messages[-1]['content'].startswith('LATEST-USER')
    assert all(message.get('tool_call_id') != 'call-1' for message in messages)


def test_context_manager_rejects_an_individually_oversized_user_message():
    payload = {'model': 'small-test-model', 'messages': [{'role': 'user', 'content': 'x' * 3000}]}

    with pytest.raises(HTTPException) as error:
        _manager().prepare(payload, 'https://provider.invalid/v1')

    assert error.value.status_code == 413
    assert error.value.detail['code'] == 'request_json_char_limit_exceeded'


def test_internal_preparation_stays_below_95000_when_final_wire_cap_is_100000():
    manager = ContextManager(
        request_budget=RequestBudgetManager(
            RequestBudgetLimits(
                max_json_chars=100_000,
                max_preparation_chars=95_000,
                max_json_bytes=400_000,
            )
        )
    )
    payload = {'model': 'large-context-model', 'messages': [{'role': 'user', 'content': 'x' * 96_000}]}

    with pytest.raises(HTTPException) as error:
        manager.prepare(
            payload,
            'https://provider.invalid/v1',
            {'context_window_tokens': 100_000, 'max_output_tokens': 1_000, 'tool_calling': True},
        )

    assert error.value.status_code == 413
    assert error.value.detail['code'] == 'request_preparation_char_limit_exceeded'
    assert error.value.detail['preparation_limit_chars'] == 95_000
    assert 95_000 < error.value.detail['serialized_chars'] <= 100_000


def test_context_manager_does_not_compact_incomplete_tool_sequences():
    payload = {
        'model': 'small-test-model',
        'messages': [
            {'role': 'system', 'content': 's' * 20},
            {'role': 'user', 'content': 'u' * 200},
            {'role': 'assistant', 'tool_calls': [{'id': 'pending-call', 'type': 'function'}]},
        ],
    }

    with pytest.raises(HTTPException):
        _manager(chars=220).prepare(payload, 'https://provider.invalid/v1')


def test_context_manager_reserves_output_tokens_and_compacts_at_seventy_five_percent():
    payload = {
        'model': 'gpt-4o-mini',
        'max_tokens': 1024,
        'messages': [
            {'role': 'system', 'content': 'System'},
            {'role': 'user', 'content': 'first ' * 5000},
            {'role': 'assistant', 'content': 'answer ' * 5000},
            {'role': 'user', 'content': 'latest'},
        ],
    }

    prepared = _manager(chars=95_000).prepare(
        payload,
        'https://api.openai.com/v1',
        {'context_window_tokens': 4000, 'max_output_tokens': 2048, 'tool_calling': True},
    )

    assert prepared.metrics['reserved_output_tokens'] == 1024
    assert prepared.metrics['compacted_messages'] == 2
    assert prepared.metrics['compacted_for_context'] is True


def test_context_manager_compacts_hundreds_of_messages_at_complete_turn_boundaries():
    messages = [{'role': 'system', 'content': 'Keep the current objective.'}]
    for turn in range(100):
        messages.extend(
            [
                {'role': 'user', 'content': f'QUESTION-{turn} ' + ('requirement ' * 32)},
                {'role': 'assistant', 'tool_calls': [{'id': f'call-{turn}', 'type': 'function'}]},
                {'role': 'tool', 'tool_call_id': f'call-{turn}', 'content': f'RESULT-{turn} ' + ('detail ' * 32)},
                {'role': 'assistant', 'content': f'ANSWER-{turn} ' + ('conclusion ' * 32)},
            ]
        )
    messages.append({'role': 'user', 'content': 'LATEST-USER retain this exact request'})

    prepared = _manager(chars=95_000).prepare(
        {'model': 'gpt-4o-mini', 'messages': messages},
        'https://api.openai.com/v1',
        {'context_window_tokens': 4096, 'max_output_tokens': 512, 'tool_calling': True},
    )

    assert prepared.metrics['compacted_messages'] > 100
    assert prepared.metrics['estimated_input_tokens'] <= 4096 - 512
    assert prepared.payload['messages'][-1]['content'] == 'LATEST-USER retain this exact request'
    call_ids = {
        call['id']
        for message in prepared.payload['messages']
        for call in message.get('tool_calls', [])
    }
    tool_ids = {
        message['tool_call_id']
        for message in prepared.payload['messages']
        if message.get('role') == 'tool'
    }
    assert call_ids == tool_ids
