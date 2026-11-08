"""Chat task lifecycle endpoints used by the chat UI."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.config import ENABLE_ADMIN_CHAT_ACCESS
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.socket.main import get_event_emitter, get_user_id_from_session_pool
from open_webui.tasks import (
    get_response_streams_by_chat_id,
    list_task_ids_by_item_id,
    list_tasks,
    stop_item_tasks,
    stop_task,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.chat import get_temporary_chat, register_temporary_chat_session
from open_webui.utils.chat_id import get_temporary_chat_session_id

log = logging.getLogger(__name__)
router = APIRouter()


@router.post('/api/tasks/stop/{task_id}')
async def stop_task_endpoint(request: Request, task_id: str, user=Depends(get_admin_user)):
    try:
        return await stop_task(request.app.state.redis, task_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get('/api/tasks')
async def list_tasks_endpoint(request: Request, user=Depends(get_admin_user)):
    return {'tasks': await list_tasks(request.app.state.redis)}


@router.get('/api/tasks/chat/{chat_id:path}')
async def list_tasks_by_chat_id_endpoint(request: Request, chat_id: str, user=Depends(get_verified_user)):
    socket_id = get_temporary_chat_session_id(chat_id)
    if socket_id:
        owner = await get_temporary_chat(request.app.state.redis, chat_id)
        owner_id = owner['user_id'] if owner else get_user_id_from_session_pool(socket_id)
        if owner_id != user.id and not (user.role == 'admin' and ENABLE_ADMIN_CHAT_ACCESS):
            return {'task_ids': []}
    else:
        chat = await Chats.get_accessible_chat_by_id(chat_id, user, permission='write')
        if chat is None:
            return {'task_ids': []}

    task_ids = await list_task_ids_by_item_id(request.app.state.redis, chat_id)

    if not socket_id:
        task_ids = await Chats.filter_task_ids_by_user_id(chat, user.id, task_ids)

    log.debug('Task IDs for chat %s: %s', chat_id, task_ids)
    return {'task_ids': task_ids}


@router.post('/api/tasks/chat/{chat_id:path}/resume')
async def resume_temporary_chat(request: Request, chat_id: str, form_data: dict, user=Depends(get_verified_user)):
    session_id = form_data.get('session_id')
    if not isinstance(session_id, str) or not await register_temporary_chat_session(
        request.app.state.redis,
        chat_id,
        user.id,
        session_id,
        get_user_id_from_session_pool(session_id),
        create=True,
    ):
        raise HTTPException(status_code=404, detail='Temporary chat expired or unavailable')
    return {
        'task_ids': await list_task_ids_by_item_id(request.app.state.redis, chat_id),
        'streams': await get_response_streams_by_chat_id(request.app.state.redis, chat_id),
    }


async def finalize_pending_approval_messages(chat_id: str, chat, user) -> bool:
    messages_map = await Chats.get_messages_map_by_chat_id(chat_id) or {}
    finalized = False
    for message_id, message in messages_map.items():
        if (message.get('user_id') or chat.user_id) != user.id:
            continue
        if message.get('role') != 'assistant' or message.get('done') is not False:
            continue

        output = message.get('output')
        if isinstance(output, list):
            for item in output:
                if item.get('type') == 'function_call' and item.get('status') in {
                    'pending',
                    'queued',
                    'requires_approval',
                }:
                    item['status'] = 'rejected'
                    item.pop('approved', None)

        await Chats.upsert_message_to_chat_by_id_and_message_id(
            chat_id,
            message_id,
            {'done': True, **({'output': output} if isinstance(output, list) else {})},
            touch=False,
        )
        finalized = True

        event_emitter = await get_event_emitter(
            {
                'user_id': user.id,
                'chat_id': chat_id,
                'message_id': message_id,
            },
            update_db=False,
        )
        if event_emitter:
            await event_emitter({'type': 'chat:completion', 'data': {'done': True, 'output': output}})
            await event_emitter({'type': 'chat:tasks:cancel'})
            await event_emitter({'type': 'chat:active', 'data': {'active': False}})

    return finalized


@router.post('/api/tasks/chat/{chat_id:path}/stop')
async def stop_tasks_by_chat_id_endpoint(request: Request, chat_id: str, user=Depends(get_verified_user)):
    socket_id = get_temporary_chat_session_id(chat_id)
    chat = None
    if socket_id:
        owner = await get_temporary_chat(request.app.state.redis, chat_id)
        owner_id = owner['user_id'] if owner else get_user_id_from_session_pool(socket_id)
        if owner_id != user.id and not (user.role == 'admin' and ENABLE_ADMIN_CHAT_ACCESS):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    else:
        chat = await Chats.get_accessible_chat_by_id(chat_id, user, permission='write')
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    if socket_id:
        result = await stop_item_tasks(request.app.state.redis, chat_id)
    else:
        task_ids = await Chats.filter_task_ids_by_user_id(
            chat, user.id, await list_task_ids_by_item_id(request.app.state.redis, chat_id)
        )
        result = {'status': True, 'message': 'No tasks found.'}
        for task_id in task_ids:
            result = await stop_task(request.app.state.redis, task_id)

    if (
        not socket_id
        and str(result.get('message', '')).startswith('No tasks found')
        and await finalize_pending_approval_messages(chat_id, chat, user)
    ):
        result = {'status': True, 'message': 'Finalized pending approval message.'}

    return result
