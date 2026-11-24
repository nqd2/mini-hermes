from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from weai.model_routing import model_routing, tier_routes, tier_service


def test_catalog_contains_only_the_current_plan(monkeypatch):
    from asyncio import run

    user = SimpleNamespace(id='test', role='user')
    mock = AsyncMock(return_value=tier_service.PlanSnapshot('plus', 8))
    monkeypatch.setattr(tier_service.tier_repository, 'get_plan', mock)
    models = run(tier_service.virtual_catalog(user))
    assert [m['id'] for m in models] == ['agent-runtime-plus-small', 'agent-runtime-plus-medium', 'agent-runtime-plus-large']
    assert [m['name'] for m in models] == ['Plus Small', 'Plus Medium', 'Plus Large']
    for value in ('agent-runtime-free-small', 'provider/private', None):
        with pytest.raises(HTTPException) as failure:
            run(tier_service.authorize_model(user, value))
        assert failure.value.status_code == 403


def test_connection_reorder_preserves_approval_and_changed_target_requires_it_again():
    urls = ['https://api.1min.ai/openai/v1', 'https://openrouter.ai/api/v1']
    keys = ['plus-key', 'free-key']
    initial = model_routing.normalize_connections(urls, keys, {})
    identities = [options['connection_id'] for options in initial.values()]
    prior = [model_routing.Connection(identities[i], url, keys[i], initial[str(i)]) for i, url in enumerate(urls)]
    reordered = model_routing.normalize_connections(
        urls[::-1],
        keys[::-1],
        {
            '0': initial['1'],
            '1': initial['0'],
        },
        prior,
    )
    assert [options['connection_id'] for options in reordered.values()] == identities[::-1]

    changed = model_routing.normalize_connections(urls, ['changed-key', keys[1]], initial, prior)
    assert changed['0']['connection_id'] not in identities


@pytest.mark.parametrize(
    ('model', 'pricing', 'expected'),
    [
        ('org/test:free', {'prompt': '0', 'completion': '0'}, True),
        ('openrouter/free', {'prompt': '0', 'completion': '0'}, True),
        ('openrouter/free', {'prompt': '0', 'completion': '0', 'request': '1'}, False),
        ('openrouter/free', {}, False),
        ('org/test:free', {'prompt': '0', 'completion': '0', 'request': '1'}, False),
        ('org/test:free', {'prompt': '0', 'completion': '0', 'image': '0.01'}, False),
        ('org/test:free', {'prompt': '0'}, False),
        ('org/test:free', {}, False),
        ('org/test', {'prompt': '0', 'completion': '0'}, False),
    ],
)
def test_free_catalog_pricing(model, pricing, expected):
    assert model_routing.is_free_model(model, {'pricing': pricing}) is expected


@pytest.mark.asyncio
async def test_free_selection_filters_paid_and_requires_current_catalog(monkeypatch):
    connection = model_routing.Connection('openrouter', model_routing.OPENROUTER_URL, 'key', {})
    config = model_routing.ModelRoutingConfig(
        groups={
            group: (
                [
                    model_routing.ModelPoolCandidate(connection_id='openrouter', model_id=model_id, min_context=8000)
                    for model_id in ('org/free:free', 'org/paid')
                ]
                if group == 'agent-runtime-free'
                else []
            )
            for group in tier_service.GROUPS
        }
    )
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'openrouter': connection}))
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))
    mock_catalog = AsyncMock(
        return_value={
            'org/free:free': {'pricing': {'prompt': '0', 'completion': '0'}, 'context_length': 8000},
            'org/paid': {'pricing': {'prompt': '0', 'completion': '1'}, 'context_length': 8000},
        }
    )
    monkeypatch.setattr(model_routing, '_openrouter_catalog', mock_catalog)
    payload = {'messages': [{'role': 'user', 'content': 'Text'}], 'model': 'agent-runtime-free'}
    selection = await model_routing.select_model('test', 'agent-runtime-free', payload)
    assert selection.candidate.model_id == 'org/free:free'
    payload.update(models=['org/paid'], route='fallback', provider={'max_price': {'prompt': 50}})
    selection.apply(payload)
    assert payload['model'] == 'org/free:free'
    assert 'models' not in payload and 'route' not in payload
    assert payload['provider']['max_price']['prompt'] == 0

    # Legacy alias still selects from the consolidated agent-runtime-free pool.
    alias_selection = await model_routing.select_model('test', 'agent-runtime-free-large', payload)
    assert alias_selection.group_id == 'agent-runtime-free'
    assert alias_selection.candidate.model_id == 'org/free:free'

    mock_catalog.return_value = {}
    with pytest.raises(HTTPException) as failure:
        await model_routing.select_model('test', 'agent-runtime-free', payload)
    assert failure.value.detail['code'] == 'model_unavailable'


def _make_config(group_id: str, candidates: list[model_routing.ModelPoolCandidate]) -> model_routing.ModelRoutingConfig:
    return model_routing.ModelRoutingConfig(
        groups={group: (candidates if group == group_id else []) for group in tier_service.GROUPS}
    )


def _mock_routing_db(monkeypatch):
    class _FakeDb:
        @asynccontextmanager
        async def begin(self):
            yield

        async def execute(self, *_args, **_kwargs):
            return None

        async def get(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def _fake_get_async_db():
        yield _FakeDb()

    upsert_mock = AsyncMock()
    monkeypatch.setattr(model_routing, 'get_async_db', _fake_get_async_db)
    monkeypatch.setattr(model_routing.Config, 'upsert', upsert_mock)
    return upsert_mock


def test_normalize_connections_tier_scope_defaults_validation_and_id_stability():
    urls = ['https://api.example.com/v1', 'https://openrouter.ai/api/v1']
    keys = ['key-1', 'key-2']

    initial = model_routing.normalize_connections(urls, keys, {'0': {}, '1': {'tier_scope': None}})
    assert initial['0']['tier_scope'] == 'both'
    assert initial['1']['tier_scope'] == 'both'
    original_ids = [initial['0']['connection_id'], initial['1']['connection_id']]
    prior = [model_routing.Connection(original_ids[i], urls[i], keys[i], initial[str(i)]) for i in range(2)]

    for scope in ('free', 'plus', 'both'):
        updated = model_routing.normalize_connections(
            urls,
            keys,
            {
                '0': {**initial['0'], 'tier_scope': scope},
                '1': {**initial['1'], 'tier_scope': scope},
            },
            prior,
        )
        assert updated['0']['tier_scope'] == scope
        assert updated['1']['tier_scope'] == scope
        assert [updated['0']['connection_id'], updated['1']['connection_id']] == original_ids

    for invalid_scope in ('enterprise', '', 123, ['free']):
        with pytest.raises(HTTPException) as failure:
            model_routing.normalize_connections(urls[:1], keys[:1], {'0': {'tier_scope': invalid_scope}})
        assert failure.value.status_code == 422
        assert failure.value.detail['code'] == 'invalid_connection_tier'


@pytest.mark.asyncio
@pytest.mark.parametrize('tier_scope', ['free', 'both'])
async def test_non_openrouter_free_or_both_connection_works_in_free_group(monkeypatch, tier_scope):
    connection = model_routing.Connection(
        'custom-free',
        'https://api.custom-provider.example/v1',
        'secret-key',
        {'tier_scope': tier_scope},
    )
    config = _make_config(
        'agent-runtime-free',
        [model_routing.ModelPoolCandidate(connection_id='custom-free', model_id='custom/model-a', min_context=8000)],
    )
    upsert_mock = _mock_routing_db(monkeypatch)
    mock_catalog = AsyncMock(side_effect=AssertionError('OpenRouter catalog should not be queried'))
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'custom-free': connection}))
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(model_routing, '_openrouter_catalog', mock_catalog)
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))

    updated = await model_routing.update_model_routing_config(config)
    assert updated.version == 1
    upsert_mock.assert_awaited_once()

    payload = {
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'model': 'agent-runtime-free',
        'provider': {'max_price': {'prompt': 10}},
    }
    selection = await model_routing.select_model('user-1', 'agent-runtime-free', payload)
    assert selection.connection.id == 'custom-free'
    assert selection.candidate.model_id == 'custom/model-a'
    selection.apply(payload)
    assert payload['model'] == 'custom/model-a'
    assert 'provider' not in payload
    mock_catalog.assert_not_awaited()


@pytest.mark.asyncio
async def test_openrouter_free_connection_still_enforces_free_catalog_and_provider_caps(monkeypatch):
    connection = model_routing.Connection(
        'openrouter-free',
        model_routing.OPENROUTER_URL,
        'or-key',
        {'tier_scope': 'free'},
    )
    upsert_mock = _mock_routing_db(monkeypatch)
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'openrouter-free': connection}))
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))
    monkeypatch.setattr(
        model_routing,
        '_openrouter_catalog',
        AsyncMock(
            return_value={
                'org/free:free': {'pricing': {'prompt': '0', 'completion': '0'}, 'context_length': 8000},
                'org/paid': {'pricing': {'prompt': '0.5', 'completion': '1'}, 'context_length': 8000},
            }
        ),
    )

    paid_config = _make_config(
        'agent-runtime-free',
        [model_routing.ModelPoolCandidate(connection_id='openrouter-free', model_id='org/paid', min_context=8000)],
    )
    with pytest.raises(HTTPException) as failure:
        await model_routing.update_model_routing_config(paid_config)
    assert failure.value.status_code == 422
    assert failure.value.detail['code'] == 'invalid_free_model'

    free_config = _make_config(
        'agent-runtime-free',
        [model_routing.ModelPoolCandidate(connection_id='openrouter-free', model_id='org/free:free', min_context=8000)],
    )
    updated = await model_routing.update_model_routing_config(free_config)
    assert updated.version == 1
    upsert_mock.assert_awaited_once()

    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=free_config))
    payload = {'messages': [{'role': 'user', 'content': 'Hello'}], 'model': 'agent-runtime-free'}
    selection = await model_routing.select_model('user-1', 'agent-runtime-free', payload)
    selection.apply(payload)
    assert payload['provider'] == {
        'max_price': {'prompt': 0, 'completion': 0, 'request': 0},
        'data_collection': 'deny',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tier_scope', 'group_id'),
    [
        ('plus', 'agent-runtime-free'),
        ('free', 'agent-runtime-plus-small'),
    ],
)
async def test_mismatched_tier_scope_rejected_in_update_and_select(monkeypatch, tier_scope, group_id):
    connection = model_routing.Connection(
        'scoped-conn',
        'https://api.custom-provider.example/v1',
        'secret-key',
        {'tier_scope': tier_scope},
    )
    config = _make_config(
        group_id,
        [model_routing.ModelPoolCandidate(connection_id='scoped-conn', model_id='org/model', min_context=8000)],
    )
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'scoped-conn': connection}))
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as update_failure:
        await model_routing.update_model_routing_config(config)
    assert update_failure.value.status_code == 422
    assert update_failure.value.detail['code'] == 'invalid_model_connection'

    payload = {'messages': [{'role': 'user', 'content': 'Hello'}], 'model': group_id}
    with pytest.raises(HTTPException) as select_failure:
        await model_routing.select_model('user-1', group_id, payload)
    assert select_failure.value.status_code == 503
    assert select_failure.value.detail['code'] == 'model_unavailable'


@pytest.mark.asyncio
async def test_admin_get_routing_includes_connection_tier_scope(monkeypatch):
    connections = {
        'free-conn': model_routing.Connection('free-conn', 'https://free.example/v1', 'k1', {'tier_scope': 'free'}),
        'default-conn': model_routing.Connection('default-conn', 'https://both.example/v1', 'k2', {}),
    }
    config = model_routing.ModelRoutingConfig(groups={group: [] for group in tier_service.GROUPS})
    monkeypatch.setattr(tier_routes, 'get_model_routing_config', AsyncMock(return_value=config))
    monkeypatch.setattr(tier_routes, 'get_connections', AsyncMock(return_value=connections))

    result = await tier_routes.get_routing(_admin=SimpleNamespace(id='admin'))
    assert result['connections'] == [
        {'id': 'free-conn', 'url': 'free.example', 'enabled': True, 'tier_scope': 'free'},
        {'id': 'default-conn', 'url': 'both.example', 'enabled': True, 'tier_scope': 'both'},
    ]


@pytest.mark.asyncio
async def test_list_free_catalog_models_filters_free_and_extracts_capabilities(monkeypatch):
    openrouter_conn = model_routing.Connection(
        'or-free', model_routing.OPENROUTER_URL, 'or-key', {'tier_scope': 'free'}
    )
    plus_conn = model_routing.Connection(
        'or-plus', model_routing.OPENROUTER_URL, 'plus-key', {'tier_scope': 'plus'}
    )
    monkeypatch.setattr(
        model_routing,
        'get_connections',
        AsyncMock(return_value={'or-free': openrouter_conn, 'or-plus': plus_conn}),
    )
    monkeypatch.setattr(
        model_routing,
        '_openrouter_catalog',
        AsyncMock(
            return_value={
                'meta-llama/llama-3.3-70b-instruct:free': {
                    'id': 'meta-llama/llama-3.3-70b-instruct:free',
                    'name': 'Llama 3.3 70B Free',
                    'context_length': 131072,
                    'pricing': {'prompt': '0', 'completion': '0'},
                    'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['text']},
                    'supported_parameters': ['tools', 'structured_outputs'],
                },
                'nvidia/llama-nemotron-embed-vl-1b-v2:free': {
                    'id': 'nvidia/llama-nemotron-embed-vl-1b-v2:free',
                    'name': 'Nemotron Embed VL Free',
                    'context_length': 8192,
                    'pricing': {'prompt': '0', 'completion': '0'},
                    'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['embeddings']},
                },
                'google/lyria-3-pro:free': {
                    'id': 'google/lyria-3-pro:free',
                    'name': 'Lyria 3 Pro Free',
                    'context_length': 8192,
                    'pricing': {'prompt': '0', 'completion': '0'},
                    'architecture': {'input_modalities': ['text'], 'output_modalities': ['audio']},
                },
                'openai/gpt-4o': {
                    'id': 'openai/gpt-4o',
                    'name': 'GPT-4o',
                    'context_length': 128000,
                    'pricing': {'prompt': '0.005', 'completion': '0.015'},
                },
            }
        ),
    )

    result = await tier_routes.get_free_catalog(refresh=False, _admin=SimpleNamespace(id='admin'))
    assert result['models'] == [
        {
            'connection_id': 'or-free',
            'connection_url': 'openrouter.ai',
            'model_id': 'meta-llama/llama-3.3-70b-instruct:free',
            'name': 'Llama 3.3 70B Free',
            'context_length': 131072,
            'capabilities': ['text', 'image', 'tools', 'structured_output'],
        }
    ]


def test_parse_openrouter_all_providers_icons():
    from weai.model_routing.provider_icons import parse_all_providers_payload

    parsed = parse_all_providers_payload(
        {
            'data': [
                {
                    'slug': 'openai',
                    'icon': {'url': '/images/icons/OpenAI.svg', 'className': 'invert-0 dark:invert'},
                },
                {
                    'slug': 'deepseek',
                    'icon': {'url': 'https://openrouter.ai/images/icons/DeepSeek.png'},
                },
                {
                    'slug': 'bad-scheme',
                    'icon': {'url': 'javascript:alert(1)'},
                },
            ]
        }
    )
    assert parsed == {
        'openai': {'url': 'https://openrouter.ai/images/icons/OpenAI.svg', 'invert_dark': True},
        'deepseek': {'url': 'https://openrouter.ai/images/icons/DeepSeek.png', 'invert_dark': False},
    }


def test_is_text_generation_model_filters_non_text_models_and_respects_manual_override():
    from weai.utils.model_ids import is_text_generation_model

    for valid in (
        'gpt-4o',
        'gpt-4.1-mini',
        'gpt-5.4',
        'o3-mini',
        'claude-sonnet-4-20250514',
        'google/gemini-2.5-pro',
        'meta-llama/llama-3.2-11b-vision-instruct:free',
        'qwen/qwen-2.5-vl-72b-instruct:free',
        {'id': 'org/chat:free', 'architecture': {'modality': 'text+image->text', 'output_modalities': ['text']}},
        {'id': 'local/vlm', 'type': 'vlm'},
    ):
        assert is_text_generation_model(valid) is True, valid

    for invalid in (
        'text-embedding-3-small',
        'nvidia/llama-nemotron-embed-vl-1b-v2:free',
        'baai/bge-large-en-v1.5',
        'bge-reranker-v2-m3',
        'omni-moderation-latest',
        'whisper-1',
        'tts-1-hd',
        'gpt-4o-mini-tts',
        'gpt-4o-transcribe',
        'gpt-4o-realtime-preview',
        'gpt-4o-audio-preview',
        'dall-e-3',
        'gpt-image-1',
        'chatgpt-image-latest',
        'black-forest-labs/flux-1-schnell:free',
        'google/imagen-3',
        'gemini-2.5-flash-image-preview',
        'gemini-2.5-flash-image',
        'sora-2',
        'babbage-002',
        'davinci-002',
        {'id': 'custom/model:free', 'architecture': {'output_modalities': ['embeddings']}},
        {'id': 'custom/image:free', 'architecture': {'modality': 'text->image'}},
        {'id': 'local/embedder', 'type': 'embeddings'},
    ):
        assert is_text_generation_model(invalid) is False, invalid

    assert is_text_generation_model({'id': 'custom-tts-alias'}, allow_manual_override=True) is True
    assert (
        is_text_generation_model(
            {'id': 'custom-embed', 'architecture': {'output_modalities': ['embeddings']}},
            allow_manual_override=True,
        )
        is False
    )


def test_legacy_free_groups_auto_merge_into_single_free_group():
    config = model_routing.ModelRoutingConfig.model_validate(
        {
            'version': 3,
            'groups': {
                'agent-runtime-free-small': [
                    {'connection_id': 'or-free', 'model_id': 'org/a:free', 'priority': 2, 'min_context': 8192, 'enabled': True},
                    {'connection_id': 'or-free', 'model_id': 'org/b:free', 'priority': 0, 'min_context': 8192, 'enabled': False},
                ],
                'agent-runtime-free-medium': [
                    {'connection_id': 'or-free', 'model_id': 'org/a:free', 'priority': 0, 'min_context': 8192, 'enabled': True},
                    {'connection_id': 'or-free', 'model_id': 'org/b:free', 'priority': 1, 'min_context': 8192, 'enabled': True},
                ],
                'agent-runtime-free-large': [
                    {'connection_id': 'or-free', 'model_id': 'openrouter/free', 'priority': 0, 'min_context': 128000, 'enabled': True},
                ],
                'agent-runtime-plus-small': [],
                'agent-runtime-plus-medium': [],
                'agent-runtime-plus-large': [],
            },
        }
    )
    assert set(config.groups) == set(tier_service.GROUPS)
    by_id = {c.model_id: c for c in config.groups['agent-runtime-free']}
    assert set(by_id) == {'org/a:free', 'org/b:free', 'openrouter/free'}
    assert by_id['org/a:free'].priority == 0
    assert by_id['org/b:free'].enabled is True


@pytest.mark.asyncio
async def test_openrouter_free_router_supported_and_sorted_first(monkeypatch):
    openrouter_conn = model_routing.Connection(
        'or-free', model_routing.OPENROUTER_URL, 'or-key', {'tier_scope': 'free'}
    )
    catalog = {
        'zeta/model:free': {
            'id': 'zeta/model:free',
            'name': 'Zeta Free',
            'context_length': 64000,
            'pricing': {'prompt': '0', 'completion': '0'},
            'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
        },
        'openrouter/free': {
            'id': 'openrouter/free',
            'name': 'Free Models Router',
            'context_length': 200000,
            'pricing': {'prompt': '0', 'completion': '0'},
            'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['text']},
        },
    }
    monkeypatch.setattr(model_routing, 'get_connections', AsyncMock(return_value={'or-free': openrouter_conn}))
    monkeypatch.setattr(model_routing, '_openrouter_catalog', AsyncMock(return_value=catalog))
    monkeypatch.setattr(tier_service.tier_repository, 'recently_rate_limited', AsyncMock(return_value=False))

    listed = await model_routing.list_free_catalog_models()
    assert [item['model_id'] for item in listed] == ['openrouter/free', 'zeta/model:free']

    config = _make_config(
        'agent-runtime-free',
        [
            model_routing.ModelPoolCandidate(
                connection_id='or-free',
                model_id='openrouter/free',
                priority=0,
                min_context=128000,
                capabilities={'text', 'image', 'tools', 'structured_output', 'responses'},
            )
        ],
    )
    monkeypatch.setattr(model_routing, 'get_model_routing_config', AsyncMock(return_value=config))
    payload = {
        'model': 'agent-runtime-free',
        'messages': [{'role': 'user', 'content': 'Use tool'}],
        'tools': [{'type': 'function', 'function': {'name': 'ping'}}],
    }
    selection = await model_routing.select_model('user-1', 'agent-runtime-free', payload)
    assert selection.candidate.model_id == 'openrouter/free'
    selection.apply(payload)
    assert payload['model'] == 'openrouter/free'
    assert payload['provider'] == {
        'max_price': {'prompt': 0, 'completion': 0, 'request': 0},
        'data_collection': 'deny',
    }
    dispatch_ctx = selection.dispatch('user-1')
    assert dispatch_ctx.free_account == 'openrouter'
    assert dispatch_ctx.group_id == 'agent-runtime-free'
