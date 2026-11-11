import pytest
from fastapi import HTTPException

from open_webui.mini_hermes.retrieval import should_bypass_vector_indexing
from open_webui.retrieval.utils import generate_embeddings, get_embedding_function


def test_slim_chat_upload_without_embedding_skips_vector_indexing():
    assert should_bypass_vector_indexing(True, '', None) is True


def test_slim_knowledge_upload_without_embedding_still_requires_vector_indexing():
    assert should_bypass_vector_indexing(True, '', 'knowledge-1') is False


def test_configured_embedding_engine_keeps_vector_indexing():
    assert should_bypass_vector_indexing(True, 'openai', None) is False


def test_ollama_embedding_configuration_is_rejected_before_provider_call():
    with pytest.raises(HTTPException) as error:
        get_embedding_function(
            'ollama',
            'test-model',
            embedding_function=None,
            url='http://127.0.0.1:11434',
            key='',
            embedding_batch_size=1,
        )

    assert error.value.status_code == 400
    assert error.value.detail == 'Ollama embeddings are disabled.'


@pytest.mark.asyncio
async def test_ollama_embedding_request_is_rejected_before_network_io():
    with pytest.raises(HTTPException) as error:
        await generate_embeddings(
            engine='ollama',
            model='test-model',
            text='test input',
            url='http://127.0.0.1:11434',
        )

    assert error.value.status_code == 400
    assert error.value.detail == 'Ollama embeddings are disabled.'
