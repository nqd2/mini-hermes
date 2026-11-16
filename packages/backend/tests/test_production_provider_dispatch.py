"""Retired provider models must never reach a network dispatcher."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.embeddings import generate_embeddings


def _request_with_model(*, direct: bool = False) -> Request:
    model = {'id': 'legacy-ollama', 'owned_by': 'ollama'}
    app = SimpleNamespace(state=SimpleNamespace(MODELS={model['id']: model}))
    request = Request({'type': 'http', 'app': app})
    if direct:
        request.state.direct = True
        request.state.model = model
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
async def test_ollama_chat_model_is_rejected_before_dispatch(direct):
    with pytest.raises(HTTPException) as error:
        await generate_chat_completion(
            _request_with_model(direct=direct),
            {'model': 'legacy-ollama', 'messages': []},
            SimpleNamespace(id='user-1', role='admin'),
        )
    assert error.value.status_code == 400
    assert error.value.detail == 'Ollama models are not supported.'


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
async def test_ollama_embedding_model_is_rejected_before_dispatch(direct):
    with pytest.raises(HTTPException) as error:
        await generate_embeddings(
            _request_with_model(direct=direct),
            {'model': 'legacy-ollama', 'input': 'hello'},
            SimpleNamespace(id='user-1', role='admin'),
        )
    assert error.value.status_code == 400
    assert error.value.detail == 'Ollama models are not supported.'
