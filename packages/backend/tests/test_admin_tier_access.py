import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from weai.model_routing import tier_service
from weai.routers import tasks as tasks_router
from weai.utils import context_compaction

main_module = importlib.import_module('weai.main')


def test_virtual_catalog_returns_all_four_groups_for_admin():
    admin = SimpleNamespace(id='admin-1', role='admin')
    models = asyncio.run(tier_service.virtual_catalog(admin))
    assert [m['id'] for m in models] == [
        'agent-runtime-free',
        'agent-runtime-plus-small',
        'agent-runtime-plus-medium',
        'agent-runtime-plus-large',
    ]
    assert [m['name'] for m in models] == [
        'Free',
        'Plus Small',
        'Plus Medium',
        'Plus Large',
    ]


def test_virtual_catalog_and_legacy_free_alias_authorization_for_free_user(monkeypatch):
    user = SimpleNamespace(id='free-user-1', role='user')
    monkeypatch.setattr(
        tier_service.tier_repository,
        'get_plan',
        AsyncMock(return_value=tier_service.PlanSnapshot('free', 0)),
    )
    models = asyncio.run(tier_service.virtual_catalog(user))
    assert [m['id'] for m in models] == ['agent-runtime-free']
    assert [m['name'] for m in models] == ['Free']

    for allowed_id in (
        'agent-runtime-free',
        'agent-runtime-free-small',
        'agent-runtime-free-medium',
        'agent-runtime-free-large',
    ):
        asyncio.run(tier_service.authorize_model(user, allowed_id))


def test_get_models_prepends_all_four_tier_models_for_admin(monkeypatch):
    admin = SimpleNamespace(id='admin-1', role='admin')
    raw_models = [
        {'id': 'gpt-5.4', 'name': 'GPT 5.4'},
        {'id': 'agent-runtime-free', 'name': 'Duplicate Should Dedupe'},
    ]
    monkeypatch.setattr(main_module, 'get_all_models', AsyncMock(return_value=raw_models))
    monkeypatch.setattr(main_module, 'get_filtered_models', AsyncMock(side_effect=lambda models, user: models))
    monkeypatch.setattr(main_module.Config, 'get', AsyncMock(return_value=[]))

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    result = asyncio.run(main_module.get_models(request, refresh=False, user=admin))
    ids = [m['id'] for m in result['data']]
    assert ids[:4] == list(tier_service.GROUPS.keys())
    assert ids[4:] == ['gpt-5.4']


def test_resolve_task_models_and_generation_config_for_admin_tier(monkeypatch):
    admin = SimpleNamespace(id='admin-1', role='admin', email='admin@example.com')
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(MODELS={'gpt-5.4-nano': {'id': 'gpt-5.4-nano'}})),
    )

    selected, models = asyncio.run(
        tasks_router.resolve_task_models(request, {'model': 'agent-runtime-plus-large'}, admin)
    )
    assert selected == 'agent-runtime-plus-large'
    assert 'agent-runtime-plus-small' in models
    assert 'agent-runtime-plus-large' in models

    monkeypatch.setattr(
        tasks_router.Config,
        'get_many',
        AsyncMock(return_value={'task.model.default': '', 'task.model.external': '', 'task.model.params': {}}),
    )
    task_model_id, params = asyncio.run(
        tasks_router.get_task_model_generation_config(selected, models, user_role='admin')
    )
    assert task_model_id == 'agent-runtime-plus-small'
    assert params == {}

    monkeypatch.setattr(
        tasks_router.Config,
        'get_many',
        AsyncMock(
            return_value={
                'task.model.default': '',
                'task.model.external': 'gpt-5.4-nano',
                'task.model.params': {'temperature': 0.2},
            }
        ),
    )
    configured_id, configured_params = asyncio.run(
        tasks_router.get_task_model_generation_config(selected, models, user_role='admin')
    )
    assert configured_id == 'gpt-5.4-nano'
    assert configured_params == {'temperature': 0.2}


def test_follow_up_uses_tier_small_group_for_admin_on_tier_model(monkeypatch):
    admin = SimpleNamespace(id='admin-1', role='admin', email='admin@example.com')
    captured = {}

    async def _fake_config_get(key: str):
        if key == 'task.follow_up.enable':
            return True
        if key == 'task.follow_up.prompt_template':
            return ''
        return None

    async def _fake_config_get_many(*keys):
        return {'task.model.default': '', 'task.model.external': '', 'task.model.params': {}}

    async def _fake_inlet(request, payload, user, models):
        return payload

    async def _fake_generate(request, form_data, user):
        captured['model'] = form_data['model']
        return {'choices': []}

    monkeypatch.setenv('AGENT_RUNTIME_SMALL_TASK_MODEL', 'nonexistent-env-model')
    monkeypatch.setattr(tasks_router.Config, 'get', _fake_config_get)
    monkeypatch.setattr(tasks_router.Config, 'get_many', _fake_config_get_many)
    monkeypatch.setattr(tasks_router, 'process_pipeline_inlet_filter', _fake_inlet)
    monkeypatch.setattr(tasks_router, 'generate_chat_completion', _fake_generate)

    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
    )
    asyncio.run(
        tasks_router.generate_follow_ups(
            request,
            {
                'model': 'agent-runtime-free-large',
                'messages': [{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hello'}],
            },
            admin,
        )
    )
    assert captured['model'] == 'agent-runtime-free'


def test_context_compaction_summary_supports_admin_tier_groups(monkeypatch):
    admin = SimpleNamespace(id='admin-1', role='admin')
    captured = {}

    async def _fake_get_many(*keys):
        return {'task.model.params': {}, 'chat.context_compaction.model': ''}

    async def _fake_generate(request, form_data, user, bypass_system_prompt=False):
        captured['model'] = form_data['model']
        return {'choices': [{'message': {'content': 'Summary'}}]}

    monkeypatch.setattr(context_compaction.Config, 'get_many', _fake_get_many)
    monkeypatch.setattr('weai.utils.chat.generate_chat_completion', _fake_generate)

    request = SimpleNamespace(state=SimpleNamespace())
    result = asyncio.run(
        context_compaction._generate_summary(
            request=request,
            form_data={},
            user=admin,
            model_id='agent-runtime-plus-large',
            models={},
            compacted_messages=[{'role': 'user', 'content': 'Old message'}],
            recent_messages=[{'role': 'user', 'content': 'Recent message'}],
            previous_summary=None,
            summary_prompt_template='Summarize {{MESSAGES}}',
        )
    )
    assert result == 'Summary'
    assert captured['model'] == 'agent-runtime-plus-small'
