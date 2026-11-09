from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.response_delta import ResponseDeltaBuffer
from open_webui.utils.chat_runtime.stream_state import append_to_text_field
from open_webui.utils.response import get_response_delta_key


def _buffer(*, chunk_size=8, continuing=False, prior_output=None):
    events = []
    emitter = AsyncMock(side_effect=events.append)
    save_delta = AsyncMock()
    instance = ResponseDeltaBuffer(
        event_emitter=emitter,
        save_response_delta=save_delta,
        get_response_delta_key=get_response_delta_key,
        full_output=lambda: [{'type': 'message', 'content': []}],
        continuing=continuing,
        delta_chunk_size=chunk_size,
        prior_output=prior_output or [],
        append_to_text_field=append_to_text_field,
    )
    return instance, events, emitter, save_delta


@pytest.mark.asyncio
async def test_coalesces_same_position_deltas_and_flushes_at_chunk_size():
    buffer, events, emitter, save_delta = _buffer(chunk_size=2)

    await buffer.queue({'type': 'response.output_text.delta', 'output_index': 0, 'delta': 'a'}, 'text')
    assert events == []
    await buffer.queue({'type': 'response.output_text.delta', 'output_index': 0, 'delta': 'b'}, 'text')

    assert events == [
        {
            'type': 'response:completion',
            'data': {'type': 'response.output_text.delta', 'output_index': 0, 'delta': 'ab'},
        }
    ]
    assert save_delta.await_count == 2
    emitter.assert_awaited_once()


@pytest.mark.asyncio
async def test_flushes_pending_delta_before_switching_position():
    buffer, events, _emitter, _save_delta = _buffer()

    await buffer.queue({'type': 'response.output_text.delta', 'output_index': 0, 'delta': 'first'}, 'text')
    await buffer.queue({'type': 'response.output_text.delta', 'output_index': 1, 'delta': 'second'}, 'text')

    assert events == [
        {
            'type': 'response:completion',
            'data': {'type': 'response.output_text.delta', 'output_index': 0, 'delta': 'first'},
        }
    ]
    await buffer.flush_pending()
    assert events[-1]['data']['delta'] == 'second'


@pytest.mark.asyncio
async def test_continuation_flush_emits_combined_output_snapshot():
    buffer, events, _emitter, _save_delta = _buffer(continuing=True)

    await buffer.queue({'type': 'response.reasoning_summary_text.delta', 'delta': 'thinking'}, 'reasoning')
    await buffer.flush_pending()

    assert events == [
        {
            'type': 'chat:completion',
            'data': {
                'output': [{'type': 'message', 'content': []}],
                'type': 'response.reasoning_summary_text.delta',
            },
        }
    ]


def test_adds_prior_output_offset_and_removes_round_local_output_snapshot():
    buffer, _events, _emitter, _save_delta = _buffer(prior_output=[{}, {}])

    assert buffer.with_prior_output({'type': 'response.output_text.delta', 'output_index': 1}) == {
        'type': 'response.output_text.delta',
        'output_index': 3,
    }
    assert buffer.with_prior_output({'type': 'response.completed', 'response': {'id': 'r1', 'output': []}}) == {
        'type': 'response.completed',
        'response': {'id': 'r1'},
    }
