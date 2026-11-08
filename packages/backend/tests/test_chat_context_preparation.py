from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.context_preparation import ContextPreparationAdapters, prepare_chat_context


@pytest.mark.asyncio
async def test_prepare_chat_context_orders_prompts_and_emits_only_citable_sources():
    request = object()
    user = object()
    messages = [
        {'role': 'system', 'content': 'base system'},
        {'role': 'user', 'content': 'Which program?'} ,
    ]
    form_data = {'messages': messages}
    metadata = {}
    events = [{'status': 'existing'}]
    cited_source = {'source': {'name': 'programs.xlsx'}, 'document': ['CS program']}
    uncited_source = {'document': ['display-only metadata']}
    sources = [cited_source, uncited_source]
    applied = AsyncMock(return_value=messages + [{'role': 'system', 'content': '<sources>...</sources>'}])

    adapters = ContextPreparationAdapters(
        apply_source_context_to_messages=applied,
        build_resource_instructions=lambda current_metadata: 'Use bounded evidence',
        resolve_system_prompt=AsyncMock(return_value='model prompt'),
        get_system_message=lambda current_messages: current_messages[0],
        get_content_from_message=lambda message: message.get('content'),
        get_last_user_message=lambda current_messages: next(
            message['content'] for message in reversed(current_messages) if message['role'] == 'user'
        ),
    )

    result, result_metadata, result_events = await prepare_chat_context(
        request,
        form_data,
        metadata,
        events,
        sources,
        'Which program?',
        'template',
        user,
        adapters,
    )

    assert result is form_data
    assert result['messages'][-1]['content'] == '<sources>...</sources>'
    assert result_metadata['base_system_prompt'] == 'model prompt\nbase system\n\nUse bounded evidence'
    assert result_metadata['base_user_prompt'] == 'Which program?'
    assert result_metadata['sources'] == sources
    assert result_events == [{'status': 'existing'}, {'sources': [cited_source]}]
    applied.assert_awaited_once_with(request, messages, sources, 'Which program?')


@pytest.mark.asyncio
async def test_prepare_chat_context_skips_context_injection_without_sources():
    apply_context = AsyncMock()
    messages = [{'role': 'user', 'content': 'Hello'}]
    form_data = {'messages': messages}
    metadata = {}
    events = []
    adapters = ContextPreparationAdapters(
        apply_source_context_to_messages=apply_context,
        build_resource_instructions=lambda _metadata: None,
        resolve_system_prompt=AsyncMock(return_value=''),
        get_system_message=lambda _messages: None,
        get_content_from_message=lambda _message: None,
        get_last_user_message=lambda _messages: 'Hello',
    )

    result, result_metadata, result_events = await prepare_chat_context(
        object(), form_data, metadata, events, [], 'Hello', None, object(), adapters
    )

    assert result is form_data
    assert result['messages'] is messages
    assert result_metadata['base_system_prompt'] is None
    assert result_metadata['base_user_prompt'] == 'Hello'
    assert result_metadata['sources'] == []
    assert result_events == []
    apply_context.assert_not_awaited()
