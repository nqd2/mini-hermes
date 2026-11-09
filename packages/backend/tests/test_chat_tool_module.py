from types import SimpleNamespace

import pytest

from open_webui.tools import builtin, chat_resources
from open_webui.utils.json_codec import JSONCodec


def test_chat_tools_keep_legacy_builtin_imports():
    for name in ('search_chats', 'view_chat'):
        assert getattr(builtin, name) is getattr(chat_resources, name)


@pytest.mark.asyncio
async def test_search_chats_excludes_current_chat_and_applies_date_window(monkeypatch):
    chats = [
        SimpleNamespace(id='old', title='Old', updated_at=100),
        SimpleNamespace(id='current', title='Current', updated_at=150),
        SimpleNamespace(id='outside', title='Outside', updated_at=300),
        SimpleNamespace(id='matching', title='Budget Report', updated_at=200),
    ]
    captured = {}

    async def get_chats(**kwargs):
        captured['search'] = kwargs
        return chats

    async def get_snippets(chat_ids, user_id, query):
        captured['snippets'] = (chat_ids, user_id, query)
        return {'old': 'matched content'}

    monkeypatch.setattr(chat_resources.Chats, 'get_chats_by_user_id_and_search_text', get_chats)
    monkeypatch.setattr(chat_resources.Chats, 'get_chat_search_snippets', get_snippets)
    monkeypatch.setattr(chat_resources, 'chat_search_content_query', lambda _query: 'budget report')
    monkeypatch.setattr(chat_resources, 'chat_search_terms', lambda _query: ['budget', 'report'])

    result = JSONCodec.loads(
        await builtin.search_chats(
            query='budget report',
            count=2,
            start_timestamp=100,
            end_timestamp=250,
            __request__=object(),
            __user__={'id': 'alice'},
            __chat_id__='current',
        )
    )

    assert captured['search'] == {
        'user_id': 'alice',
        'search_text': 'budget report',
        'include_archived': False,
        'skip': 0,
        'limit': 6,
    }
    assert captured['snippets'] == (['old', 'matching'], 'alice', 'budget report')
    assert result == [
        {'id': 'old', 'title': 'Old', 'snippet': 'matched content', 'updated_at': 100},
        {'id': 'matching', 'title': 'Budget Report', 'snippet': 'Title match: Budget Report', 'updated_at': 200},
    ]


@pytest.mark.asyncio
async def test_view_chat_reconstructs_chronological_branch_and_stops_cycles(monkeypatch):
    chat = SimpleNamespace(
        id='chat-1',
        title='Previous work',
        chat={
            'history': {
                'currentId': 'reply',
                'messages': {
                    'root': {'role': 'user', 'content': 'Find the issue', 'parentId': 'reply'},
                    'reply': {'role': 'assistant', 'content': 'It is fixed', 'parentId': 'root'},
                },
            }
        },
        updated_at=20,
        created_at=10,
    )

    async def get_chat(_chat_id, _user_id):
        return chat

    monkeypatch.setattr(chat_resources.Chats, 'get_chat_by_id_and_user_id', get_chat)

    result = JSONCodec.loads(await builtin.view_chat('chat-1', __request__=object(), __user__={'id': 'alice'}))

    assert result == {
        'id': 'chat-1',
        'title': 'Previous work',
        'messages': [
            {'role': 'user', 'content': 'Find the issue'},
            {'role': 'assistant', 'content': 'It is fixed'},
        ],
        'updated_at': 20,
        'created_at': 10,
    }
