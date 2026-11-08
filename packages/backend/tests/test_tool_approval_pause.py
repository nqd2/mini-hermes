from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware, tool_approval
from open_webui.utils.chat_runtime import resource_execution


@pytest.mark.asyncio
async def test_pause_for_tool_approval_persists_queue_and_emits_active(monkeypatch):
    output = [
        {'type': 'function_call', 'id': 'call-1', 'status': 'completed'},
        {'type': 'function_call', 'call_id': 'call-2', 'status': 'in_progress'},
        {'type': 'function_call', 'call_id': 'call-3', 'status': 'rejected'},
        {'type': 'function_call', 'call_id': 'call-done', 'status': 'completed'},
        {'type': 'function_call_output', 'call_id': 'call-done', 'status': 'completed'},
        {
            'type': 'message',
            'status': 'completed',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'Unverified resource claim.'}],
        },
    ]
    metadata = {
        'chat_id': 'chat-1',
        'message_id': 'assistant-1',
        'session_id': 'session-1',
        'tool_approval': {'mode': 'ask'},
        'tool_ids': ['tool-1'],
        'skill_ids': ['skill-1'],
        'terminal_id': 'terminal-1',
        'tool_servers': ['server-1'],
        'filter_ids': ['filter-1'],
        'features': {'code_interpreter': True},
        'variables': {'name': 'Ada'},
        'files': [{'id': 'file-1'}],
        'params': {'tool_approval_mode': 'ask'},
        'resource_execution_requested': True,
    }
    upsert = AsyncMock()
    emitted = []

    async def get_emitter(_metadata, *, update_db):
        assert update_db is False

        async def emit(event):
            emitted.append(event)

        return emit

    monkeypatch.setattr(tool_approval.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    monkeypatch.setattr(tool_approval, 'get_event_emitter', get_emitter)
    monkeypatch.setattr(resource_execution, 'resource_task_state_metadata', lambda _metadata: {'checkpoint': 'cp-1'})
    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')

    await middleware.pause_for_tool_approval('chat-1', 'assistant-1', output, {}, metadata)

    upsert.assert_awaited_once_with(
        'chat-1',
        'assistant-1',
        {
            'done': False,
            'output': output[:-1],
            'meta': {
                'mode': 'ask',
                'checkpoint': 'cp-1',
                'session_id': 'session-1',
                'tool_ids': ['tool-1'],
                'skill_ids': ['skill-1'],
                'terminal_id': 'terminal-1',
                'tool_servers': ['server-1'],
                'filter_ids': ['filter-1'],
                'features': {'code_interpreter': True},
                'variables': {'name': 'Ada'},
                'files': [{'id': 'file-1'}],
                'params': {'tool_approval_mode': 'ask'},
            },
        },
        touch=False,
    )
    assert output[0]['call_id'] == 'call-1'
    assert output[0]['status'] == 'pending'
    assert output[1]['status'] == 'queued'
    assert output[2]['status'] == 'rejected'
    assert output[3]['status'] == 'completed'
    assert emitted == [{'type': 'chat:active', 'data': {'active': True}}]
    assert middleware.pause_for_tool_approval is tool_approval.pause_for_tool_approval
