from unittest.mock import AsyncMock

import pytest

from open_webui.utils import middleware
from open_webui.utils.chat_runtime import image_generation


@pytest.mark.asyncio
async def test_chat_image_generation_attaches_result_and_context(monkeypatch):
    request = object()
    user = object()
    emitter = AsyncMock()
    image_files = [{'id': 'image-1', 'url': '/files/image-1.png'}]
    generate_images = AsyncMock(return_value=image_files)
    upload_files = AsyncMock()

    async def get_config(key):
        return key == 'image_generation.enable'

    monkeypatch.setattr(image_generation, 'is_saved_chat_id', lambda chat_id: False)
    monkeypatch.setattr(image_generation.Config, 'get', AsyncMock(side_effect=get_config))
    monkeypatch.setattr(image_generation, 'image_generations', generate_images)
    monkeypatch.setattr(image_generation, 'upload_files_to_terminal', upload_files)
    form_data = {
        'model': 'chat-model',
        'terminal_id': 'terminal-1',
        'messages': [{'role': 'user', 'content': 'a quiet red fox'}],
    }
    extra_params = {
        '__metadata__': {'chat_id': 'temporary:session-1', 'message_id': 'message-1'},
        '__event_emitter__': emitter,
    }

    result = await middleware.chat_image_generation_handler(request, form_data, extra_params, user)

    assert result is form_data
    generate_images.assert_awaited_once()
    assert generate_images.await_args.kwargs['form_data'].prompt == 'a quiet red fox'
    upload_files.assert_awaited_once_with(
        request,
        user,
        image_files,
        {'chat_id': 'temporary:session-1', 'message_id': 'message-1', 'terminal_id': 'terminal-1'},
        extra_params,
    )
    assert result['messages'][0]['role'] == 'system'
    assert 'generated images are shown in chat' in result['messages'][0]['content']
    assert [call.args[0]['type'] for call in emitter.await_args_list] == ['status', 'files']
    assert emitter.await_args_list[1].args[0]['data']['files'] == [
        {'type': 'image', **image_files[0]},
    ]


@pytest.mark.asyncio
async def test_chat_image_generation_without_chat_or_emitter_is_a_noop(monkeypatch):
    config_get = AsyncMock()
    monkeypatch.setattr(image_generation.Config, 'get', config_get)
    form_data = {'messages': []}

    result = await middleware.chat_image_generation_handler(
        request=object(),
        form_data=form_data,
        extra_params={'__metadata__': {}, '__event_emitter__': None},
        user=object(),
    )

    assert result is form_data
    config_get.assert_not_awaited()
