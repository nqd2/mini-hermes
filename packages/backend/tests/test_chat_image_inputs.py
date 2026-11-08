from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware
from open_webui.utils.chat_runtime import image_inputs


def test_get_images_from_messages_groups_latest_message_first():
    messages = [
        {
            'role': 'user',
            'files': [
                {'type': 'image', 'url': 'older.png'},
                {'type': 'file', 'content_type': 'image/jpeg', 'url': 'older-photo.jpg'},
            ],
        },
        {'role': 'assistant', 'files': [{'type': 'image', 'url': 'newest.png'}]},
        {'role': 'user', 'files': [{'type': 'file', 'content_type': 'text/plain', 'url': 'notes.txt'}]},
    ]

    assert middleware.get_images_from_messages(messages) == [['newest.png'], ['older.png', 'older-photo.jpg']]


@pytest.mark.asyncio
async def test_get_image_urls_converts_data_uri_and_ignores_non_image_deltas(monkeypatch):
    store_image = AsyncMock(return_value='stored/image.png')
    request = object()
    metadata = {'chat_id': 'chat-1'}
    user = object()
    monkeypatch.setattr(image_inputs, 'get_image_url_from_base64', store_image)

    urls = await middleware.get_image_urls(
        [
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,abc'}},
            {'type': 'image_url', 'image_url': {'url': 'https://example.test/photo.jpg'}},
            {'type': 'text', 'text': 'ignore'},
            {'type': 'image_url', 'image_url': {'url': ''}},
        ],
        request=request,
        metadata=metadata,
        user=user,
    )

    assert urls == ['stored/image.png', 'https://example.test/photo.jpg']
    store_image.assert_awaited_once_with(
        request, 'data:image/png;base64,abc', metadata, user
    )


@pytest.mark.asyncio
async def test_convert_url_images_preserves_shape_detail_and_failed_items(monkeypatch):
    user = object()
    calls = []

    async def to_base64(url, *, user):
        calls.append(url)
        if url.endswith('/missing'):
            return None
        if url.endswith('/broken'):
            raise RuntimeError('fetch failed')
        return 'data:image/png;base64,converted'

    monkeypatch.setattr(image_inputs, 'get_image_base64_from_url', to_base64)
    dict_image = {
        'type': 'image_url',
        'image_url': {'url': 'https://example.test/dict-image', 'detail': 'high', 'ignored': 'metadata'},
    }
    string_image = {
        'type': 'input_image',
        'image_url': 'https://example.test/string-image',
        'custom': True,
    }
    missing_image = {'type': 'image_url', 'image_url': {'url': 'https://example.test/missing'}}
    broken_image = {'type': 'image_url', 'image_url': {'url': 'https://example.test/broken'}}
    inline_image = {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,inline'}}
    form_data = {
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'keep'},
                    dict_image,
                    string_image,
                    missing_image,
                    broken_image,
                    inline_image,
                    'unstructured content',
                ],
            }
        ]
    }

    result = await middleware.convert_url_images_to_base64(form_data, user=user)

    assert result is form_data
    assert result['messages'][0]['content'] == [
        {'type': 'text', 'text': 'keep'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,converted', 'detail': 'high'}},
        {
            'type': 'input_image',
            'image_url': 'data:image/png;base64,converted',
            'custom': True,
        },
        missing_image,
        broken_image,
        inline_image,
        'unstructured content',
    ]
    assert calls == [
        'https://example.test/dict-image',
        'https://example.test/string-image',
        'https://example.test/missing',
        'https://example.test/broken',
    ]
