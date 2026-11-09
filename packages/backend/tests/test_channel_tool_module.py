from types import SimpleNamespace

import pytest

from open_webui.tools import builtin, channel_resources
from open_webui.utils.json_codec import JSONCodec

CHANNEL_TOOL_NAMES = (
    'search_channels',
    'search_channel_messages',
    'view_channel_message',
    'view_channel_thread',
)


def test_channel_tools_keep_legacy_builtin_imports():
    for name in CHANNEL_TOOL_NAMES:
        assert getattr(builtin, name) is getattr(channel_resources, name)


@pytest.mark.asyncio
async def test_search_channel_messages_keeps_member_scope_and_timestamp_units(monkeypatch):
    channel = SimpleNamespace(id='channel-1', name='Engineering')
    message = SimpleNamespace(
        id='message-1',
        channel_id='channel-1',
        parent_id='parent-1',
        content='The matching detail appears here.',
        created_at=2_500_000_000,
    )
    captured = {}

    async def get_channels(user_id):
        captured['user_id'] = user_id
        return [channel]

    async def search_messages(**kwargs):
        captured['search'] = kwargs
        return [message]

    monkeypatch.setattr(channel_resources.Channels, 'get_channels_by_user_id', get_channels)
    monkeypatch.setattr(channel_resources.Messages, 'search_messages_by_channel_ids', search_messages)

    result = JSONCodec.loads(
        await builtin.search_channel_messages(
            query='matching',
            count=3,
            start_timestamp=2,
            end_timestamp=4,
            __request__=object(),
            __user__={'id': 'alice'},
        )
    )

    assert captured['user_id'] == 'alice'
    assert captured['search'] == {
        'channel_ids': ['channel-1'],
        'query': 'matching',
        'start_timestamp': 2_000_000_000,
        'end_timestamp': 4_000_000_000,
        'limit': 3,
    }
    assert result == [
        {
            'channel_id': 'channel-1',
            'channel_name': 'Engineering',
            'message_id': 'message-1',
            'content_snippet': 'The matching detail appears here.',
            'is_thread_reply': True,
            'parent_id': 'parent-1',
            'created_at': 2_500_000_000,
        }
    ]


@pytest.mark.asyncio
async def test_view_channel_message_denies_nonmembers_before_returning_content(monkeypatch):
    message = SimpleNamespace(
        id='message-1',
        channel_id='private-channel',
        content='private team content',
        user_id='bob',
        parent_id=None,
        reply_count=0,
        created_at=1,
        updated_at=2,
        user=None,
    )

    async def get_message(_message_id):
        return message

    async def get_channel(_channel_id):
        return SimpleNamespace(id='private-channel', name='Private')

    async def get_channels(_user_id):
        return []

    monkeypatch.setattr(channel_resources.Messages, 'get_message_by_id', get_message)
    monkeypatch.setattr(channel_resources.Channels, 'get_channel_by_id', get_channel)
    monkeypatch.setattr(channel_resources.Channels, 'get_channels_by_user_id', get_channels)

    result = JSONCodec.loads(
        await builtin.view_channel_message(
            'message-1',
            __request__=object(),
            __user__={'id': 'alice'},
        )
    )

    assert result == {'error': 'Access denied'}
