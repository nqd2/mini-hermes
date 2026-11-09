from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.skill_context import (
    ChatSkillContextAdapters,
    ChatSkillServices,
    prepare_chat_skill_context,
)
from open_webui.utils.misc import add_or_update_system_message, get_last_user_message, set_last_user_message_content
from open_webui.utils.skills import extract_skill_ids_from_messages, strip_skill_mentions


@dataclass
class _Skill:
    id: str
    name: str
    description: str = ''
    is_active: bool = True
    version_id: str = 'current-version'


def _services(**overrides):
    values = {
        'get_db_skills': AsyncMock(return_value=[]),
        'get_skill_snapshot': AsyncMock(),
        'skill_overview': AsyncMock(return_value={'files': []}),
        'format_skill_content': lambda overview, skill_id, tools_enabled: f'{skill_id}:{tools_enabled}',
        'get_terminal_json': AsyncMock(return_value=[]),
        'get_terminal_skill': AsyncMock(),
        'format_terminal_skill_context': lambda skill, skill_id, tools_enabled: f'{skill_id}:{tools_enabled}',
        'format_terminal_skill_manifest_entry': lambda skill: f'<terminal>{skill["id"]}</terminal>',
    }
    values.update(overrides)
    return ChatSkillServices(**values)


def _adapters(config_get, load_services):
    return ChatSkillContextAdapters(
        config_get=config_get,
        extract_skill_ids_from_messages=extract_skill_ids_from_messages,
        strip_skill_mentions=strip_skill_mentions,
        add_or_update_system_message=add_or_update_system_message,
        get_last_user_message=get_last_user_message,
        set_last_user_message_content=set_last_user_message_content,
        load_services=load_services,
    )


@pytest.mark.asyncio
async def test_activated_database_skill_loads_pinned_version_and_strips_only_resolved_mention():
    skill = _Skill(id='reporting', name='Reporting')
    services = _services(
        get_db_skills=AsyncMock(return_value=[skill]),
        get_skill_snapshot=AsyncMock(
            return_value={'content': '# Reporting workflow', 'data': {'files': [{'path': 'SKILL.md'}]}}
        ),
        skill_overview=AsyncMock(return_value={'files': [], 'content': '# Reporting workflow'}),
        format_skill_content=lambda overview, skill_id, tools_enabled: (
            f'{overview["content"]}:{skill_id}:{tools_enabled}'
        ),
    )
    config_get = AsyncMock(return_value=False)
    form_data = {
        'messages': [{'role': 'user', 'content': 'Use <$reporting|the report skill> for this.'}],
        'skill_ids': [],
    }
    metadata = {'chat_context': {'skill_versions': {'reporting': 'pinned-version'}}}

    result = await prepare_chat_skill_context(
        object(),
        form_data,
        type('User', (), {'id': 'alice'})(),
        {'info': {'meta': {}}},
        metadata,
        {},
        use_builtin_tools=False,
        payload_tools=None,
        terminal_id=None,
        adapters=_adapters(config_get, lambda: services),
    )

    services.get_db_skills.assert_awaited_once_with(user_id='alice', ids=['reporting'])
    services.get_skill_snapshot.assert_awaited_once_with(skill, 'pinned-version')
    assert result.skill_ids == ['reporting']
    assert result.resolved_skill_ids == {'reporting'}
    assert result.prompt == 'Use the report skill for this.'
    assert metadata['chat_context']['skill_versions']['reporting'] == 'pinned-version'
    assert metadata['chat_context']['mentioned_skill_ids'] == ['reporting']
    assert form_data['messages'][0]['role'] == 'system'
    assert 'Reporting workflow' in form_data['messages'][0]['content']
    assert next(message for message in form_data['messages'] if message['role'] == 'user')['content'] == (
        'Use the report skill for this.'
    )
    assert 'skill_ids' not in form_data


@pytest.mark.asyncio
async def test_lazy_loading_lists_unselected_skill_as_manifest_without_loading_its_body():
    selected = _Skill(id='selected', name='Selected')
    listed = _Skill(id='listed', name='Listed', description='Available on demand')
    services = _services(
        get_db_skills=AsyncMock(return_value=[selected, listed]),
        get_skill_snapshot=AsyncMock(
            return_value={'content': 'selected body', 'data': {'files': [{'path': 'SKILL.md'}]}}
        ),
    )
    config_get = AsyncMock(return_value=True)
    form_data = {
        'messages': [{'role': 'user', 'content': 'Use <$selected|Selected> now.'}],
        'skill_ids': ['listed'],
    }

    result = await prepare_chat_skill_context(
        object(),
        form_data,
        type('User', (), {'id': 'alice'})(),
        {'info': {'meta': {'builtinTools': {'skills': True}}}},
        {},
        {},
        use_builtin_tools=True,
        payload_tools=None,
        terminal_id=None,
        adapters=_adapters(config_get, lambda: services),
    )

    services.get_db_skills.assert_awaited_once_with(user_id='alice')
    services.get_skill_snapshot.assert_awaited_once_with(selected, 'current-version')
    assert result.skill_ids == ['listed', 'selected']
    assert result.resolved_skill_ids == {'selected', 'listed'}
    assert result.model_builtin_tools == {'skills': True}
    assert any('<id>listed</id>' in message['content'] for message in form_data['messages'])
    assert next(message for message in form_data['messages'] if message['role'] == 'user')['content'] == (
        'Use Selected now.'
    )


@pytest.mark.asyncio
async def test_selected_terminal_skill_decodes_name_and_loads_context():
    terminal_skill = {'id': 'terminal:project%20notes', 'name': 'Project notes'}
    services = _services(
        get_terminal_json=AsyncMock(return_value=[terminal_skill]),
        get_terminal_skill=AsyncMock(return_value={'name': 'Project notes', 'resources': []}),
    )
    form_data = {'messages': [{'role': 'user', 'content': 'Read this skill'}], 'skill_ids': [terminal_skill['id']]}
    request = object()
    user = type('User', (), {'id': 'alice', 'model_dump': lambda self: {'id': 'alice'}})()
    metadata = {'terminal_id': 'terminal-1'}
    extra_params = {}

    result = await prepare_chat_skill_context(
        request,
        form_data,
        user,
        {'info': {'meta': {}}},
        metadata,
        extra_params,
        use_builtin_tools=False,
        payload_tools=None,
        terminal_id='terminal-1',
        adapters=_adapters(AsyncMock(return_value=False), lambda: services),
    )

    services.get_terminal_json.assert_awaited_once_with(request, user, metadata, '/skills', extra_params)
    services.get_terminal_skill.assert_awaited_once_with(
        request,
        {'id': 'alice'},
        metadata,
        'project notes',
        extra_params,
        overview=True,
        tools_enabled=False,
    )
    assert result.resolved_skill_ids == {terminal_skill['id']}
    assert form_data['messages'][0]['content'].endswith(f'{terminal_skill["id"]}:False')
