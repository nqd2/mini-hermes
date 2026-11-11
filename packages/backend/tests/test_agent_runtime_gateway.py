from __future__ import annotations

import pytest
from fastapi import HTTPException

from open_webui.agent_runtime.gateway import ProviderGateway
from open_webui.agent_runtime.request_budget import RequestBudgetLimits, RequestBudgetManager


def test_provider_gateway_validates_json_after_escaping_and_serialization():
    gateway = ProviderGateway(
        request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_chars=180, max_json_bytes=720))
    )
    payload = {'model': 'small', 'messages': [{'role': 'user', 'content': '"' * 100}]}

    with pytest.raises(HTTPException) as error:
        gateway.prepare(payload, 'https://provider.invalid/v1')

    assert error.value.status_code == 413
    assert error.value.detail['code'] == 'request_json_char_limit_exceeded'
    assert error.value.detail['serialized_chars'] > 180


def test_final_serialized_json_limit_is_100000_characters():
    budget = RequestBudgetManager(
        RequestBudgetLimits(max_json_chars=100_000, max_preparation_chars=95_000, max_json_bytes=400_000)
    )
    exactly_at_limit = '{"x":"' + ('x' * 99_992) + '"}'
    one_over_limit = '{"x":"' + ('x' * 99_993) + '"}'

    assert len(exactly_at_limit) == 100_000
    assert budget.validate(exactly_at_limit)['serialized_chars'] == 100_000
    with pytest.raises(HTTPException) as error:
        budget.validate(one_over_limit)

    assert error.value.status_code == 413
    assert error.value.detail['limit_chars'] == 100_000
    assert error.value.detail['serialized_chars'] == 100_001


def test_environment_cannot_raise_the_approved_request_character_caps(monkeypatch):
    monkeypatch.setenv('AGENT_REQUEST_MAX_JSON_CHARS', '150000')
    monkeypatch.setenv('AGENT_REQUEST_PREPARATION_MAX_JSON_CHARS', '120000')

    limits = RequestBudgetLimits.from_environment()

    assert limits.max_json_chars == 100_000
    assert limits.preparation_limit_chars == 95_000


@pytest.mark.asyncio
async def test_provider_gateway_sends_only_a_validated_prepared_json_body():
    gateway = ProviderGateway(
        request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_chars=500, max_json_bytes=2000))
    )
    prepared = gateway.prepare(
        {'model': 'small', 'messages': [{'role': 'user', 'content': 'hello'}]},
        'https://provider.invalid/v1',
    )
    session = _Session()

    response = await gateway.send(session, 'POST', 'https://provider.invalid/v1/chat/completions', prepared)

    assert response is session.response
    assert session.arguments['data'] == prepared.wire_body
    assert session.arguments['method'] == 'POST'


class _Session:
    def __init__(self):
        self.response = object()
        self.arguments = None

    async def request(self, **kwargs):
        self.arguments = kwargs
        return self.response
