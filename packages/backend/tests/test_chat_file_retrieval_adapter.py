from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.file_retrieval import chat_completion_files_handler


def request_with_retrieval_functions():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                EMBEDDING_FUNCTION=lambda query, prefix, user: (query, prefix, user),
                RERANKING_FUNCTION=None,
            )
        )
    )


@pytest.mark.asyncio
async def test_generates_queries_retrieves_sources_and_emits_status_events(monkeypatch):
    files = [
        {'id': 'file-1', 'type': 'file', 'context': 'query'},
        {'id': 'legacy', 'type': 'filesystem', 'context': 'full'},
    ]
    body = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'Find the matching row'}],
        'metadata': {'chat_id': 'chat-1', 'files': files},
    }
    source = {
        'document': ['matching row'],
        'metadata': [{'source': 'source-1'}],
        'source': {'id': 'file-1', 'name': 'report.xlsx'},
    }
    generate_queries = AsyncMock(return_value={'choices': [{'message': {'content': '{"queries": ["matching row"]}'}}]})
    get_sources = AsyncMock(return_value=[source])
    config = AsyncMock(
        return_value={
            'rag.top_k': 5,
            'rag.top_k_reranker': 3,
            'rag.relevance_threshold': 0.2,
            'rag.hybrid_bm25_weight': 0.4,
            'rag.enable_hybrid_search': True,
            'rag.full_context': False,
        }
    )
    emitter = AsyncMock()
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.generate_queries', generate_queries)
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.get_sources_from_items', get_sources)
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.Config.get_many', config)

    returned_body, flags = await chat_completion_files_handler(
        request_with_retrieval_functions(), body, {'__event_emitter__': emitter}, object()
    )

    assert returned_body is body
    assert flags == {'sources': [source]}
    generate_queries.assert_awaited_once()
    assert get_sources.await_args.kwargs['items'] == [files[0]]
    assert get_sources.await_args.kwargs['queries'] == ['matching row']
    assert get_sources.await_args.kwargs['k'] == 5
    assert get_sources.await_args.kwargs['full_context'] is False
    assert [call.args[0]['data']['action'] for call in emitter.await_args_list] == [
        'queries_generated',
        'sources_retrieved',
    ]
    assert emitter.await_args_list[-1].args[0]['data']['count'] == 1


@pytest.mark.asyncio
async def test_full_context_skips_query_generation_and_uses_user_prompt(monkeypatch):
    body = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'Read this document'}],
        'metadata': {'files': [{'id': 'file-1', 'type': 'file', 'context': 'full'}]},
    }
    generate_queries = AsyncMock()
    get_sources = AsyncMock(return_value=[])
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.generate_queries', generate_queries)
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.get_sources_from_items', get_sources)
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.file_retrieval.Config.get_many',
        AsyncMock(return_value={'rag.full_context': False}),
    )

    _, flags = await chat_completion_files_handler(
        request_with_retrieval_functions(), body, {'__event_emitter__': AsyncMock()}, object()
    )

    generate_queries.assert_not_awaited()
    assert get_sources.await_args.kwargs['queries'] == ['Read this document']
    assert get_sources.await_args.kwargs['full_context'] is True
    assert flags == {'sources': []}


@pytest.mark.asyncio
async def test_query_generation_error_falls_back_to_the_user_prompt(monkeypatch):
    body = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'Use this query instead'}],
        'metadata': {'files': [{'id': 'file-1', 'type': 'file'}]},
    }
    get_sources = AsyncMock(return_value=[])
    emitter = AsyncMock()
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.file_retrieval.generate_queries',
        AsyncMock(side_effect=RuntimeError('query model unavailable')),
    )
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.get_sources_from_items', get_sources)
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.Config.get_many', AsyncMock(return_value={}))

    _, flags = await chat_completion_files_handler(
        request_with_retrieval_functions(), body, {'__event_emitter__': emitter}, object()
    )

    assert get_sources.await_args.kwargs['queries'] == ['Use this query instead']
    assert emitter.await_args_list[0].args[0]['data']['queries'] == []
    assert flags == {'sources': []}


@pytest.mark.asyncio
async def test_malformed_query_response_is_used_as_a_fallback_query(monkeypatch):
    raw_query_response = 'Search for the matching title and date'
    body = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'Summarize this file'}],
        'metadata': {'files': [{'id': 'file-1', 'type': 'file'}]},
    }
    get_sources = AsyncMock(return_value=[])
    emitter = AsyncMock()
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.file_retrieval.generate_queries',
        AsyncMock(return_value={'choices': [{'message': {'content': raw_query_response}}]}),
    )
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.get_sources_from_items', get_sources)
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.Config.get_many', AsyncMock(return_value={}))

    await chat_completion_files_handler(
        request_with_retrieval_functions(), body, {'__event_emitter__': emitter}, object()
    )

    assert get_sources.await_args.kwargs['queries'] == [raw_query_response]
    assert emitter.await_args_list[0].args[0]['data']['queries'] == [raw_query_response]


@pytest.mark.asyncio
async def test_retrieval_failure_returns_no_sources_and_reports_zero(monkeypatch):
    body = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'Find this value'}],
        'metadata': {'files': [{'id': 'file-1', 'type': 'file'}]},
    }
    emitter = AsyncMock()
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.file_retrieval.generate_queries',
        AsyncMock(return_value={'choices': [{'message': {'content': '{"queries": ["value"]}'}}]}),
    )
    monkeypatch.setattr(
        'open_webui.utils.chat_runtime.file_retrieval.get_sources_from_items',
        AsyncMock(side_effect=RuntimeError('retrieval unavailable')),
    )
    monkeypatch.setattr('open_webui.utils.chat_runtime.file_retrieval.Config.get_many', AsyncMock(return_value={}))

    _, flags = await chat_completion_files_handler(
        request_with_retrieval_functions(), body, {'__event_emitter__': emitter}, object()
    )

    assert flags == {'sources': []}
    assert emitter.await_args_list[-1].args[0]['data'] == {
        'action': 'sources_retrieved',
        'count': 0,
        'done': True,
    }


def test_middleware_keeps_legacy_file_retrieval_handler_import():
    from open_webui.utils import middleware

    assert middleware.chat_completion_files_handler is chat_completion_files_handler
