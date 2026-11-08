from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.responses import JSONResponse

from open_webui.routers.retrieval import SearchForm
from open_webui.utils import middleware


@pytest.mark.asyncio
async def test_chat_web_search_falls_back_to_user_query_and_attaches_results(monkeypatch):
    emitter = AsyncMock()
    request = SimpleNamespace(state=SimpleNamespace())
    form_data = {
        'model': 'chat-model',
        'messages': [{'role': 'user', 'content': 'recent robotics research'}],
    }
    generate_queries = AsyncMock(return_value=JSONResponse({'detail': 'task model unavailable'}, status_code=404))
    process_web_search = AsyncMock(
        return_value={
            'docs': ['robotics paper abstract'],
            'filenames': ['https://example.test/paper'],
            'items': [{'title': 'Robotics paper'}],
        }
    )
    monkeypatch.setattr(middleware, 'generate_queries', generate_queries)
    monkeypatch.setattr(middleware, 'process_web_search', process_web_search)

    result = await middleware.chat_web_search_handler(
        request,
        form_data,
        {'__event_emitter__': emitter, '__chat_id__': 'chat-1'},
        user=object(),
    )

    assert result['files'] == [
        {
            'docs': ['robotics paper abstract'],
            'name': 'recent robotics research',
            'type': 'web_search',
            'urls': ['https://example.test/paper'],
            'queries': ['recent robotics research'],
        }
    ]
    search_form = process_web_search.await_args.args[1]
    assert isinstance(search_form, SearchForm)
    assert search_form.queries == ['recent robotics research']
    assert generate_queries.await_args.args[1]['chat_id'] == 'chat-1'
    assert [call.args[0]['data']['action'] for call in emitter.await_args_list] == [
        'web_search',
        'web_search_queries_generated',
        'web_search',
    ]
