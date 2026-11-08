"""Image generation and editing workflow for chat requests."""

import logging

from fastapi import HTTPException
from starlette.responses import JSONResponse

from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.routers.images import CreateImageForm, EditImageForm, image_edits, image_generations
from open_webui.routers.tasks import generate_image_prompt
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.chat_runtime.image_inputs import get_images_from_messages
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import add_or_update_system_message, get_last_user_message, get_message_list
from open_webui.utils.terminals import upload_files_to_terminal

log = logging.getLogger(__name__)


async def run_chat_image_generation(request, form_data, extra_params, user):
    metadata = extra_params.get('__metadata__', {})
    chat_id = metadata.get('chat_id')
    event_emitter = extra_params.get('__event_emitter__')
    if not chat_id or not isinstance(chat_id, str) or not event_emitter:
        return form_data

    is_channel_chat = chat_id.startswith('channel:')
    image_metadata = {
        'message_id': metadata.get('message_id'),
        **({'channel_id': chat_id.removeprefix('channel:')} if is_channel_chat else {'chat_id': chat_id}),
    }
    terminal_metadata = {**metadata, 'terminal_id': form_data.get('terminal_id')}
    message_list = await _get_message_list(chat_id, metadata, form_data, user)
    user_message = get_last_user_message(message_list)
    prompt = user_message
    input_images = _get_input_images(message_list)

    editing = len(input_images) > 0 and await Config.get('images.edit.enable')
    if not editing and not await Config.get('image_generation.enable'):
        return form_data

    if is_saved_chat_id(chat_id):
        await event_emitter({'type': 'status', 'data': {'description': 'Creating image', 'done': False}})

    if editing:
        system_message_content = await _edit_chat_images(
            request,
            prompt,
            input_images,
            image_metadata,
            terminal_metadata,
            extra_params,
            user,
            event_emitter,
        )
    elif not await Config.get('image_generation.enable'):
        await event_emitter(
            {
                'type': 'status',
                'data': {'description': 'Image generation is disabled', 'done': True},
            }
        )
        system_message_content = (
            '<context>Image generation was requested but the feature is currently disabled by the administrator, '
            'so no image was created. Let the user know that image generation is currently unavailable.</context>'
        )
    else:
        if await Config.get('image_generation.prompt.enable'):
            prompt = await _generate_prompt(request, form_data, metadata, user, user_message)
        system_message_content = await _create_chat_images(
            request,
            prompt,
            image_metadata,
            terminal_metadata,
            extra_params,
            user,
            event_emitter,
        )

    if system_message_content:
        form_data['messages'] = add_or_update_system_message(system_message_content, form_data['messages'])
    return form_data


async def _get_message_list(chat_id, metadata, form_data, user):
    if not is_saved_chat_id(chat_id):
        return form_data.get('messages', [])

    chat = await Chats.get_accessible_chat_by_id(chat_id, user, permission='write')
    messages_map = chat.chat.get('history', {}).get('messages', {})
    message_id = metadata.get('user_message_id') or chat.chat.get('history', {}).get('currentId')
    return get_message_list(messages_map, message_id)


def _get_input_images(message_list):
    message_images = get_images_from_messages(message_list)
    return [image for image_group in message_images[:2] for image in image_group]


async def _edit_chat_images(
    request,
    prompt,
    input_images,
    image_metadata,
    terminal_metadata,
    extra_params,
    user,
    event_emitter,
):
    try:
        images = await image_edits(
            request=request,
            form_data=EditImageForm(**{'prompt': prompt, 'image': input_images}),
            metadata=image_metadata,
            user=user,
        )
        await upload_files_to_terminal(request, user, images, terminal_metadata, extra_params)
        await event_emitter({'type': 'status', 'data': {'description': 'Image created', 'done': True}})
        await _emit_images(event_emitter, images)
        return (
            '<context>The edited images are shown in chat. Use terminal_path with terminal tools or '
            f'display_file when available. Images: {JSONCodec.dumps(images)}</context>'
        )
    except Exception as e:
        log.debug(e)
        error_message = _get_error_message(e)
        await event_emitter(
            {
                'type': 'status',
                'data': {'description': 'An error occurred while generating an image', 'done': True},
            }
        )
        return (
            '<context>Image generation was attempted but failed. The system is currently unable to generate the '
            f'image. Tell the user that the following error occurred: {error_message}</context>'
        )


async def _generate_prompt(request, form_data, metadata, user, user_message):
    try:
        response = await generate_image_prompt(
            request,
            {
                'model': form_data['model'],
                'messages': form_data['messages'],
                'chat_id': metadata.get('chat_id'),
            },
            user,
        )
        if isinstance(response, JSONResponse):
            try:
                error_body = JSONCodec.loads(response.body)
                detail = error_body.get('detail', 'Image prompt generation failed')
            except Exception:
                detail = 'Image prompt generation failed'
            raise Exception(detail)

        content = response['choices'][0]['message']['content']
        try:
            bracket_start = content.rfind('{')
            bracket_end = content.rfind('}') + 1
            if bracket_start == -1 or bracket_end == -1:
                raise Exception('No JSON object found in the response')
            result = JSONCodec.loads(content[bracket_start:bracket_end])
            return result.get('prompt', [])
        except Exception:
            return user_message
    except Exception as e:
        log.exception(e)
        return user_message


async def _create_chat_images(
    request,
    prompt,
    image_metadata,
    terminal_metadata,
    extra_params,
    user,
    event_emitter,
):
    try:
        images = await image_generations(
            request=request,
            form_data=CreateImageForm(**{'prompt': prompt}),
            metadata=image_metadata,
            user=user,
        )
        await upload_files_to_terminal(request, user, images, terminal_metadata, extra_params)
        await event_emitter({'type': 'status', 'data': {'description': 'Image created', 'done': True}})
        await _emit_images(event_emitter, images)
        return (
            '<context>The generated images are shown in chat. Use terminal_path with terminal tools or '
            f'display_file when available. Images: {JSONCodec.dumps(images)}</context>'
        )
    except Exception as e:
        log.debug(e)
        error_message = _get_error_message(e)
        await event_emitter(
            {
                'type': 'status',
                'data': {'description': 'An error occurred while generating an image', 'done': True},
            }
        )
        return (
            '<context>Image generation was attempted but failed because of an error. The system is currently '
            f'unable to generate the image. Tell the user that the following error occurred: {error_message}</context>'
        )


async def _emit_images(event_emitter, images):
    await event_emitter(
        {
            'type': 'files',
            'data': {'files': [{'type': 'image', **image} for image in images]},
        }
    )


def _get_error_message(error):
    if not isinstance(error, HTTPException):
        return ''
    if error.detail and isinstance(error.detail, dict):
        return error.detail.get('message', str(error.detail))
    return str(error.detail)
