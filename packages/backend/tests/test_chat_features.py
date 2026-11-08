from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.features import ChatFeatureAdapters, process_chat_features


def _adapters(config_get, **overrides):
    async def unexpected(*_args, **_kwargs):
        pytest.fail('feature adapter should not be called')

    def message_update(*_args, **_kwargs):
        pytest.fail('message adapter should not be called')

    values = {
        'config_get': config_get,
        'has_permission': unexpected,
        'add_memory_context': unexpected,
        'web_search_handler': unexpected,
        'image_generation_handler': unexpected,
        'add_or_update_system_message': message_update,
        'add_or_update_user_message': message_update,
        'default_voice_prompt': 'default voice',
        'default_code_interpreter_prompt': 'execute code',
        'code_interpreter_pyodide_prompt': ' filesystem',
    }
    values.update(overrides)
    return ChatFeatureAdapters(**values)


@pytest.mark.asyncio
async def test_process_chat_features_preserves_legacy_order_and_prompt_placement():
    order = []
    config_values = {
        'task.voice.prompt.enable': True,
        'task.voice.prompt_template': 'voice prompt',
        'memories.enable': True,
        'memories.system_context.enable': True,
        'web.search.enable': True,
        'code_interpreter.engine': 'pyodide',
        'code_interpreter.prompt_template': '',
    }

    async def config_get(key, *_args):
        return config_values.get(key)

    def add_system(prompt, messages, **kwargs):
        order.append(('voice' if prompt == 'voice prompt' else 'native-code-context', kwargs))
        return messages + [{'role': 'system', 'content': prompt}]

    def add_user(prompt, messages):
        order.append(('code-interpreter', prompt))
        return messages + [{'role': 'user', 'content': prompt}]

    async def add_memory(request, form_data, user, model):
        order.append(('memory', request, user, model))
        return form_data

    async def web_search(request, form_data, extra_params, user):
        order.append(('web-search', request, user))
        return form_data

    async def image_generation(request, form_data, extra_params, user):
        order.append(('image-generation', request, user))
        return form_data

    adapters = _adapters(
        config_get,
        add_memory_context=add_memory,
        web_search_handler=web_search,
        image_generation_handler=image_generation,
        add_or_update_system_message=add_system,
        add_or_update_user_message=add_user,
    )
    request = object()
    user = type('User', (), {'id': 'alice', 'role': 'admin'})()
    model = {'id': 'model-1'}
    metadata = {'params': {'function_calling': 'legacy'}}
    extra_params = {}
    features = {
        'voice': True,
        'memory': True,
        'web_search': True,
        'image_generation': True,
        'code_interpreter': True,
    }
    form_data = {
        'messages': [{'role': 'user', 'content': 'draw and search'}],
        'features': features,
    }

    result = await process_chat_features(request, form_data, user, model, metadata, extra_params, adapters)

    assert result is form_data
    assert [entry[0] for entry in order] == [
        'voice',
        'memory',
        'web-search',
        'image-generation',
        'code-interpreter',
    ]
    assert order[-1][1] == 'execute code filesystem'
    assert result['messages'][1] == {'role': 'system', 'content': 'voice prompt'}
    assert extra_params['__features__'] is features
    assert 'features' not in form_data


@pytest.mark.asyncio
async def test_process_chat_features_skips_forced_search_and_generation_for_native_tools():
    async def config_get(key, *_args):
        return {'web.search.enable': True, 'code_interpreter.engine': 'jupyter'}.get(key)

    web_search = AsyncMock()
    image_generation = AsyncMock()
    add_system = AsyncMock()
    add_user = AsyncMock()
    adapters = _adapters(
        config_get,
        web_search_handler=web_search,
        image_generation_handler=image_generation,
        add_or_update_system_message=add_system,
        add_or_update_user_message=add_user,
    )
    form_data = {
        'messages': [{'role': 'user', 'content': 'search and draw'}],
        'features': {'web_search': True, 'image_generation': True, 'code_interpreter': True},
    }

    await process_chat_features(
        object(),
        form_data,
        type('User', (), {'role': 'admin'})(),
        {},
        {'params': {'function_calling': 'native'}},
        {},
        adapters,
    )

    web_search.assert_not_awaited()
    image_generation.assert_not_awaited()
    add_system.assert_not_called()
    add_user.assert_not_called()


@pytest.mark.asyncio
async def test_process_chat_features_rechecks_permissions_for_client_feature_flags():
    async def config_get(key, *_args):
        return {
            'memories.enable': True,
            'memories.system_context.enable': True,
            'web.search.enable': True,
        }.get(key)

    has_permission = AsyncMock(return_value=False)
    memory = AsyncMock()
    web_search = AsyncMock()
    image_generation = AsyncMock()
    adapters = _adapters(
        config_get,
        has_permission=has_permission,
        add_memory_context=memory,
        web_search_handler=web_search,
        image_generation_handler=image_generation,
    )
    form_data = {
        'messages': [],
        'features': {'memory': True, 'web_search': True, 'image_generation': True},
    }
    user = type('User', (), {'id': 'alice', 'role': 'user'})()

    await process_chat_features(object(), form_data, user, {}, {'params': {'function_calling': 'legacy'}}, {}, adapters)

    assert has_permission.await_count == 3
    assert [call.args[1] for call in has_permission.await_args_list] == [
        'features.memories',
        'features.web_search',
        'features.image_generation',
    ]
    memory.assert_not_awaited()
    web_search.assert_not_awaited()
    image_generation.assert_not_awaited()
