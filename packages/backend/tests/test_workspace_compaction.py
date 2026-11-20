import json
from types import SimpleNamespace

import pytest

from weai.utils.chat_runtime.resource_execution import ResourceAnswerGate
from weai.utils.context_compaction import compact_messages_for_request, prune_oversized_tool_history


def _tool_loop_messages(num_batches: int, payload_size: int) -> list[dict]:
    messages: list[dict] = [
        {'role': 'system', 'content': 'System instructions'},
        {'role': 'user', 'content': 'Translate the attached thesis and verify formatting.'},
    ]
    for i in range(num_batches):
        call_id = f'call_{i}'
        messages.append(
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': call_id,
                        'type': 'function',
                        'function': {'name': 'run_workspace_command', 'arguments': json.dumps({'command': f'step {i}'})},
                    }
                ],
            }
        )
        messages.append(
            {
                'role': 'tool',
                'tool_call_id': call_id,
                'content': json.dumps({'stdout': f'batch-{i}-' + ('x' * payload_size)}),
            }
        )
    return messages


def test_prune_oversized_tool_history_keeps_under_95k_and_preserves_latest_batch():
    messages = _tool_loop_messages(num_batches=8, payload_size=16_000)
    payload = {'model': 'gpt-4.1', 'messages': messages}
    assert len(json.dumps(payload)) > 95_000

    pruned, changed = prune_oversized_tool_history(payload, 95_000)
    assert changed is True
    assert len(json.dumps(pruned)) <= 95_000

    # Active user prompt and the most recent tool batch remain completely intact.
    assert pruned['messages'][1]['content'] == 'Translate the attached thesis and verify formatting.'
    latest_tool = pruned['messages'][-1]
    assert latest_tool['role'] == 'tool'
    assert 'batch-7-' in latest_tool['content']
    assert 'compacted' not in latest_tool['content']

    # Every tool message retains its tool_call_id pairing.
    call_ids = [
        tc['id']
        for m in pruned['messages']
        if m.get('role') == 'assistant'
        for tc in (m.get('tool_calls') or [])
    ]
    tool_ids = [m['tool_call_id'] for m in pruned['messages'] if m.get('role') == 'tool']
    assert tool_ids == call_ids


@pytest.mark.asyncio
async def test_mid_turn_compaction_triggers_on_95k_char_budget_even_when_global_toggle_off(monkeypatch):
    from weai.utils import context_compaction

    async def fake_load_config():
        return {
            'enable': False,
            'token_threshold': 120_000,
            'token_cap': 200_000,
            'retention_percentage': 40,
            'prompt_template': '',
        }

    called = {}

    async def fake_generate_summary(
        request, form_data, user, model_id, models, compacted_messages, recent_messages, *args, **kwargs
    ):
        called['compacted'] = len(compacted_messages)
        called['recent'] = len(recent_messages)
        return 'Completed steps 0-4 in work/chunks; ready to finish.'

    monkeypatch.setattr(context_compaction, '_load_config', fake_load_config)
    monkeypatch.setattr(context_compaction, '_generate_summary', fake_generate_summary)

    messages = _tool_loop_messages(num_batches=6, payload_size=14_000)
    form_data = {'model': 'gpt-4.1', 'messages': messages}
    result_messages, summary, compacted = await compact_messages_for_request(
        request=SimpleNamespace(state=SimpleNamespace()),
        form_data=form_data,
        user=SimpleNamespace(id='alice'),
        messages=messages,
        metadata={},
        model_id='gpt-4.1',
        models={'gpt-4.1': {}},
        bypass_system_prompt=True,
        force_char_budget=95_000,
    )
    assert summary == 'Completed steps 0-4 in work/chunks; ready to finish.'
    assert len(compacted) > 0
    assert called['compacted'] > 0
    assert len(result_messages) < len(messages)


def test_budget_exhaustion_fallback_guides_continue_response():
    text = ResourceAnswerGate.fallback_text('partial', 'model_call_or_time_budget_exhausted')
    assert 'Continue Response' in text
    assert 'workspace' in text.lower()


def test_prune_oversized_tool_history_compacts_large_older_args_and_respects_token_budget():
    # Build tool batches with huge older assistant arguments + modest tool outputs
    messages: list[dict] = [
        {'role': 'system', 'content': 'System instructions'},
        {'role': 'user', 'content': 'Process chunks.'},
    ]
    for i in range(4):
        call_id = f'call_{i}'
        messages.append(
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': call_id,
                        'type': 'function',
                        'function': {
                            'name': 'run_workspace_subtasks',
                            'arguments': json.dumps({'tasks': [{'i': j, 'pad': 'y' * 200} for j in range(30)]}),
                        },
                    }
                ],
            }
        )
        messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': 'ok'})

    payload = {'model': 'gpt-6-luna', 'messages': messages}
    pruned, changed = prune_oversized_tool_history(payload, 95_000, max_tokens=2_500, model='gpt-6-luna')
    assert changed is True
    # Older assistant tool_call arguments before latest batch should be compacted into valid JSON.
    first_args = json.loads(pruned['messages'][2]['tool_calls'][0]['function']['arguments'])
    assert first_args['_compacted'] is True
    # Latest assistant tool_call batch stays untouched.
    latest_args = json.loads(pruned['messages'][-2]['tool_calls'][0]['function']['arguments'])
    assert '_compacted' not in latest_args
