from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.mini_hermes import model_routing, tier_service


def test_catalog_contains_only_the_current_plan(monkeypatch):
    from asyncio import run

    user = SimpleNamespace(id='test', role='user')
    mock = AsyncMock(return_value=tier_service.PlanSnapshot('plus', 8))
    monkeypatch.setattr(tier_service.tier_repository, 'get_plan', mock)
    models = run(tier_service.virtual_catalog(user))
    assert [m['id'] for m in models] == ['mh-plus-small', 'mh-plus-medium', 'mh-plus-large']
    assert [m['name'] for m in models] == ['Plus Small', 'Plus Medium', 'Plus Large']
    for value in ('mh-free-small', 'provider/private', None):
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
                if group == 'mh-free-small'
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
    payload = {'messages': [{'role': 'user', 'content': 'Text'}], 'model': 'mh-free-small'}
    selection = await model_routing.select_model('test', 'mh-free-small', payload)
    assert selection.candidate.model_id == 'org/free:free'
    payload.update(models=['org/paid'], route='fallback', provider={'max_price': {'prompt': 50}})
    selection.apply(payload)
    assert payload['model'] == 'org/free:free'
    assert 'models' not in payload and 'route' not in payload
    assert payload['provider']['max_price']['prompt'] == 0

    mock_catalog.return_value = {}
    with pytest.raises(HTTPException) as failure:
        await model_routing.select_model('test', 'mh-free-small', payload)
    assert failure.value.detail['code'] == 'model_unavailable'
