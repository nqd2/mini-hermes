from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import pytest


@pytest.mark.asyncio
async def test_direct_chat_model_resolution_preserves_request_binding(monkeypatch):
    main = importlib.import_module('open_webui.main')

    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    direct_model = {'id': 'direct', 'direct': True, 'info': {'meta': {'capabilities': {'usage': True}}}}
    bind = AsyncMock()
    monkeypatch.setattr(main, '_set_direct_model', bind)

    model, model_info, fallback, missing_base = await main._resolve_chat_model(
        request, SimpleNamespace(id='user', role='user'), 'direct', direct_model
    )

    assert model is direct_model
    assert model_info is None and fallback is None and missing_base is False
    bind.assert_awaited_once_with(request, direct_model, SimpleNamespace(id='user', role='user'))


@pytest.mark.asyncio
async def test_missing_base_model_checks_fallback_access(monkeypatch):
    main = importlib.import_module('open_webui.main')
    model = {'id': 'custom'}
    fallback = {'id': 'fallback'}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={
        'custom': model, 'fallback': fallback,
    })))
    user = SimpleNamespace(id='user', role='user')
    model_info = SimpleNamespace(base_model_id='missing', model_copy=lambda **kwargs: 'access-info')
    monkeypatch.setattr(main.Models, 'get_model_by_id', AsyncMock(return_value=model_info))
    monkeypatch.setattr(main.Config, 'get', AsyncMock(return_value='fallback'))
    monkeypatch.setattr(main, 'ENABLE_CUSTOM_MODEL_FALLBACK', True)
    monkeypatch.setattr(main, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    access = AsyncMock()
    monkeypatch.setattr(main, 'check_model_access', access)

    resolved, info, alternate, missing = await main._resolve_chat_model(request, user, 'custom', {})

    assert resolved is model and info is model_info and alternate is fallback and missing
    assert access.await_count == 2
    access.assert_any_await(user, model, model_info='access-info')
    access.assert_any_await(user, fallback)
