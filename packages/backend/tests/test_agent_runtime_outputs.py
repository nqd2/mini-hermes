from __future__ import annotations

import pytest

from open_webui.agent_runtime.outputs import ToolOutputManager
from open_webui.mini_hermes.context_budget import estimate_tokens


def test_tool_output_manager_keeps_prefix_suffix_and_reports_full_size():
    manager = ToolOutputManager(max_tokens=32)
    source = 'HEAD ' + ('alpha beta gamma delta ' * 80) + ' TAIL'

    output = manager.truncate(source, model='unknown-test-model')

    assert output.truncated is True
    assert output.original_chars == len(source)
    assert output.original_tokens > 32
    assert output.text.startswith('HEAD ')
    assert output.text.endswith(' TAIL')
    assert '[output truncated;' in output.text


def test_tool_output_manager_leaves_small_output_unchanged():
    source = {'ok': True, 'message': 'small'}

    output = ToolOutputManager(max_tokens=100).truncate(source)

    assert output.truncated is False
    assert output.value == source


@pytest.mark.asyncio
async def test_tool_output_manager_obeys_remaining_request_budgets():
    source = 'HEAD ' + ('alpha beta gamma delta ' * 300) + ' TAIL'

    output = await ToolOutputManager(max_tokens=1000, max_chars=8000).process(
        source,
        model='unknown-test-model',
        max_tokens=24,
        max_chars=140,
    )

    assert output.truncated is True
    assert len(output.text) <= 140
    assert estimate_tokens(output.text, 'unknown-test-model') <= 24
    assert output.text.startswith('HEAD ')
    assert output.text.endswith(' TAIL')
