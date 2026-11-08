"""Apply authorized chat features before tool resolution and retrieval."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatFeatureAdapters:
    config_get: Callable[..., Awaitable[Any]]
    has_permission: Callable[..., Awaitable[bool]]
    add_memory_context: Callable[..., Awaitable[dict]]
    web_search_handler: Callable[..., Awaitable[dict]]
    image_generation_handler: Callable[..., Awaitable[dict]]
    add_or_update_system_message: Callable[..., list[dict]]
    add_or_update_user_message: Callable[..., list[dict]]
    default_voice_prompt: str
    default_code_interpreter_prompt: str
    code_interpreter_pyodide_prompt: str


async def process_chat_features(request, form_data, user, model, metadata, extra_params, adapters: ChatFeatureAdapters):
    features = form_data.pop('features', None) or {}
    extra_params['__features__'] = features
    if not features:
        return form_data

    if features.get('voice') and await adapters.config_get('task.voice.prompt.enable'):
        template = await adapters.config_get('task.voice.prompt_template')
        form_data['messages'] = adapters.add_or_update_system_message(
            template or adapters.default_voice_prompt,
            form_data['messages'],
        )

    form_data = await _apply_memory_feature(request, form_data, user, model, features, adapters)
    form_data = await _apply_web_search_feature(request, form_data, user, metadata, extra_params, features, adapters)
    form_data = await _apply_image_generation_feature(
        request,
        form_data,
        user,
        metadata,
        extra_params,
        features,
        adapters,
    )
    form_data = await _apply_code_interpreter_feature(form_data, metadata, features, adapters)
    return form_data


async def _apply_memory_feature(request, form_data, user, model, features, adapters):
    if not (
        features.get('memory')
        and await adapters.config_get('memories.enable')
        and await adapters.config_get('memories.system_context.enable')
    ):
        return form_data

    if getattr(user, 'role', None) != 'admin' and not await adapters.has_permission(
        getattr(user, 'id', ''),
        'features.memories',
        await adapters.config_get('user.permissions'),
    ):
        return form_data
    return await adapters.add_memory_context(request, form_data, user, model)


async def _apply_web_search_feature(request, form_data, user, metadata, extra_params, features, adapters):
    if not features.get('web_search') or not await adapters.config_get('web.search.enable'):
        return form_data
    if getattr(user, 'role', None) != 'admin' and not await adapters.has_permission(
        getattr(user, 'id', ''),
        'features.web_search',
        await adapters.config_get('user.permissions'),
    ):
        return form_data
    if metadata.get('params', {}).get('function_calling') == 'legacy':
        return await adapters.web_search_handler(request, form_data, extra_params, user)
    return form_data


async def _apply_image_generation_feature(request, form_data, user, metadata, extra_params, features, adapters):
    if not features.get('image_generation'):
        return form_data
    if getattr(user, 'role', None) != 'admin' and not await adapters.has_permission(
        getattr(user, 'id', ''),
        'features.image_generation',
        await adapters.config_get('user.permissions'),
    ):
        return form_data
    if metadata.get('params', {}).get('function_calling') == 'legacy':
        return await adapters.image_generation_handler(request, form_data, extra_params, user)
    return form_data


async def _apply_code_interpreter_feature(form_data, metadata, features, adapters):
    if not features.get('code_interpreter'):
        return form_data
    engine = await adapters.config_get('code_interpreter.engine', 'pyodide')
    legacy_function_calling = metadata.get('params', {}).get('function_calling') == 'legacy'
    if legacy_function_calling:
        template = await adapters.config_get('code_interpreter.prompt_template')
        prompt = template if template != '' else adapters.default_code_interpreter_prompt
        if engine != 'jupyter':
            prompt += adapters.code_interpreter_pyodide_prompt
        form_data['messages'] = adapters.add_or_update_user_message(prompt, form_data['messages'])
    elif engine != 'jupyter':
        form_data['messages'] = adapters.add_or_update_system_message(
            adapters.code_interpreter_pyodide_prompt,
            form_data['messages'],
            append=True,
        )
    return form_data
