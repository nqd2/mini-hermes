from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.users import Users
from open_webui.socket.main import get_event_emitter
from open_webui.utils.ask_user import normalize_ask_user_request
from open_webui.utils.json_codec import JSONCodec


class ResolveToolCallForm(BaseModel):
    call_id: str
    action: Literal['approve', 'reject', 'answer']
    answers: Any | None = None
    timed_out: bool = False


def _validated_user_answers(raw_arguments: Any, answers: Any) -> dict:
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail='Answers must be keyed by question id.')
    try:
        arguments = raw_arguments
        for _ in range(2):
            if isinstance(arguments, str):
                arguments = JSONCodec.loads(arguments)
        request = normalize_ask_user_request(arguments)
    except (TypeError, ValueError, JSONCodec.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail='Saved user question is invalid.') from exc
    questions = request['questions']
    if set(answers) != {question['id'] for question in questions}:
        raise HTTPException(status_code=400, detail='Answer every question exactly once.')
    validated = {}
    for question in questions:
        answer = answers[question['id']]
        if not isinstance(answer, dict):
            raise HTTPException(status_code=400, detail='Invalid user answer.')
        if answer.get('type') == 'option':
            index = answer.get('option_index')
            options = question['options']
            if (
                type(index) is not int or not 0 <= index < len(options)
                or answer.get('label') != options[index]['label']
                or answer.get('description') != options[index]['description']
            ):
                raise HTTPException(status_code=400, detail='Selected option does not match the question.')
            validated[question['id']] = {
                'type': 'option', 'option_index': index, **options[index],
            }
        elif answer.get('type') == 'other' and question['allow_other']:
            text = answer.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise HTTPException(status_code=400, detail='Free-form answer must be 1–2000 characters.')
            validated[question['id']] = {'type': 'other', 'text': text.strip()}
        else:
            raise HTTPException(status_code=400, detail='Invalid user answer type.')
    return validated


def pending_tool_calls(output):
    result_ids = {item.get('call_id') for item in output if item.get('type') == 'function_call_output'}
    return [
        item
        for item in output
        if item.get('type') == 'function_call'
        and (item.get('call_id') or item.get('id')) not in result_ids
        and item.get('status') in {'pending', 'queued', 'requires_approval', 'in_progress'}
    ]


def tool_continuation():
    return {
        'type': 'message',
        'id': f'msg_{uuid4().hex}',
        'status': 'in_progress',
        'role': 'assistant',
        'content': [{'type': 'output_text', 'text': ''}],
    }


async def complete_tool_call(chat_id, message_id, call, result, *, output_filter=None):
    async with Chats.edit_message_output(chat_id, message_id) as message:
        if not message:
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
        output = message.get('output') or []
        saved = next((item for item in pending_tool_calls(output) if item.get('call_id') == call['call_id']), None)
        if saved is None or saved.get('status') != 'in_progress':
            raise HTTPException(status_code=409, detail='Tool call is no longer running.')
        saved.update(call)
        output.append(result)
        if output_filter:
            output = output_filter(output)
            message['output'] = output
        pending = pending_tool_calls(output)
        if result.get('status') == 'incomplete':
            message['done'] = True
        elif not pending:
            # Only the transaction completing the last call may start the model again.
            output.append(tool_continuation())
        else:
            next_approval = next((item for item in pending if not item.get('approved')), None)
            if next_approval is not None:
                next_approval['status'] = 'pending'
        return output


async def pause_for_tool_approval(
    chat_id: str, message_id: str, output: list[dict], form_data: dict, metadata: dict
) -> None:
    """Persist the ordered approval queue and notify the active chat session."""
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate, resource_task_state_metadata

    result_call_ids = {
        item.get('call_id') for item in output if item.get('type') == 'function_call_output' and item.get('call_id')
    }
    has_pending_approval = False
    for item in output:
        if item.get('type') == 'function_call' and not item.get('call_id') and item.get('id'):
            item['call_id'] = item['id']

        if (
            item.get('type') == 'function_call'
            and item.get('call_id')
            and item.get('call_id') not in result_call_ids
            and item.get('status') != 'rejected'
        ):
            if not has_pending_approval:
                item['status'] = 'pending'
                has_pending_approval = True
            elif item.get('status') in {'in_progress', 'completed'}:
                item['status'] = 'queued'

    persisted_output = ResourceAnswerGate(metadata).present(output)
    await Chats.upsert_message_to_chat_by_id_and_message_id(
        chat_id,
        message_id,
        {
            'done': False,
            'output': persisted_output,
            'meta': {
                **(metadata.get('tool_approval') or {}),
                **resource_task_state_metadata(metadata),
                'session_id': metadata.get('session_id'),
                'tool_ids': metadata.get('tool_ids') or [],
                'skill_ids': metadata.get('skill_ids') or [],
                'terminal_id': metadata.get('terminal_id'),
                'tool_servers': metadata.get('tool_servers'),
                'filter_ids': metadata.get('filter_ids') or [],
                'features': metadata.get('features') or {},
                'variables': metadata.get('variables') or {},
                'files': metadata.get('files') or [],
                'params': metadata.get('params') or {},
            },
        },
        touch=False,
    )
    event_emitter = await get_event_emitter(metadata, update_db=False)
    if event_emitter:
        await event_emitter({'type': 'chat:active', 'data': {'active': True}})


async def resolve_tool_call_output(
    chat_id: str,
    message_id: str,
    form_data: ResolveToolCallForm,
    user,
    db: AsyncSession | None = None,
) -> dict:
    chat = await Chats.get_accessible_chat_by_id(chat_id, user, db=db, permission='write')
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    async with Chats.edit_message_output(chat_id, message_id) as message:
        if not message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

        if (message.get('user_id') or chat.user_id) != user.id:
            raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)

        output = message.get('output') or []
        if not isinstance(output, list):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Message has no resolvable output.')

        function_call = next(
            (
                item
                for item in output
                if item.get('type') == 'function_call' and (item.get('call_id') or item.get('id')) == form_data.call_id
            ),
            None,
        )
        if not function_call:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Tool call not found.')
        function_call.setdefault('call_id', form_data.call_id)
        tool_name = function_call.get('name')
        if tool_name == 'ask_user' and form_data.timed_out:
            raise HTTPException(status_code=400, detail='Saved user questions do not expire automatically.')
        validated_answers = (
            _validated_user_answers(function_call.get('arguments'), form_data.answers)
            if tool_name == 'ask_user' and form_data.action == 'answer'
            else None
        )

        resolved_output = next(
            (
                item for item in output
                if item.get('type') == 'function_call_output' and item.get('call_id') == form_data.call_id
            ),
            None,
        )
        if resolved_output and tool_name == 'ask_user' and form_data.action == 'answer':
            try:
                previous = JSONCodec.loads(resolved_output['output'][0]['text'])
            except (KeyError, IndexError, TypeError, JSONCodec.JSONDecodeError):
                previous = None
            if previous == {'status': 'answered', 'answers': validated_answers}:
                return {'chat': chat, 'output': output, 'resume': None}
            raise HTTPException(status_code=409, detail='User question was answered differently.')
        if resolved_output and tool_name == 'ask_user' and form_data.action == 'reject':
            if function_call.get('status') == 'rejected' and resolved_output.get('status') == 'rejected':
                return {'chat': chat, 'output': output, 'resume': None}
            raise HTTPException(status_code=409, detail='User question was already answered.')

        if (
            resolved_output is not None
            or function_call.get('status') not in {'pending', 'queued', 'requires_approval'}
            or function_call.get('approved') is True
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Tool call has already been resolved.')

        active = any(
            item.get('approved') is True or item.get('status') == 'in_progress' for item in pending_tool_calls(output)
        )

        if form_data.action == 'approve':
            if tool_name == 'ask_user':
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail='ask_user requires an answer or deny.'
                )
            function_call['status'] = 'queued'
            function_call['approved'] = True
        elif form_data.action == 'reject':
            function_call['status'] = 'rejected'
            output.append(
                {
                    'type': 'function_call_output',
                    'id': f'fco_{form_data.call_id}',
                    'call_id': form_data.call_id,
                    'output': [{'type': 'input_text', 'text': 'Error: tool call rejected by user.'}],
                    'status': 'rejected',
                }
            )
        else:
            if tool_name != 'ask_user':
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail='Tool call does not accept answers.'
                )
            if validated_answers is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail='Answers are required for ask_user.'
                )
            function_call['status'] = 'completed'
            answer_payload = {'status': 'answered', 'answers': validated_answers}
            output.append(
                {
                    'type': 'function_call_output',
                    'id': f'fco_{form_data.call_id}',
                    'call_id': form_data.call_id,
                    'output': [{'type': 'input_text', 'text': JSONCodec.dumps(answer_payload)}],
                    'status': 'completed',
                }
            )

        message['done'] = False
        pending = pending_tool_calls(output)
        resume = None
        if not active:
            if form_data.action == 'approve':
                resume = 'tools'
            elif not pending:
                output.append(tool_continuation())
                resume = 'model'
            else:
                pending[0]['status'] = 'pending'

    event_emitter = await get_event_emitter(
        {
            'user_id': user.id,
            'chat_id': chat_id,
            'message_id': message_id,
        },
        update_db=False,
    )
    if event_emitter:
        await event_emitter({'type': 'chat:completion', 'data': {'output': output}})

    return {'chat': chat, 'output': output, 'resume': resume}


async def build_tool_approval_resume_payload(chat_id: str, message_id: str, chat=None) -> dict:
    chat = chat or await Chats.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    assistant_message = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
    if not assistant_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    user_message_id = assistant_message.get('parentId')
    user_message = await Chats.get_message_by_id_and_message_id(chat_id, user_message_id) if user_message_id else None
    if not user_message:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Tool call parent message is missing.')

    chat_data = chat.chat or {}
    message_meta = assistant_message.get('meta') if isinstance(assistant_message.get('meta'), dict) else {}
    chat_params = chat_data.get('params') if isinstance(chat_data.get('params'), dict) else {}
    if (assistant_message.get('user_id') or chat.user_id) != chat.user_id:
        chat_params = {}
    params = {
        **chat_params,
        **(message_meta.get('params') if isinstance(message_meta.get('params'), dict) else {}),
    }
    current_approval_mode = chat_params.get('tool_approval_mode')
    if current_approval_mode in {'ask', 'full'}:
        params['tool_approval_mode'] = current_approval_mode
    if 'tool_approval_mode' not in params:
        params['tool_approval_mode'] = 'ask'

    model_id = assistant_message.get('model') or next(iter(chat_data.get('models') or []), None)
    if not model_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Tool call message model is missing.')

    messages = []
    system_prompt = params.get('system')
    if not system_prompt:
        # Mirror the chat UI's system prompt fallback
        user = await Users.get_user_by_id(assistant_message.get('user_id') or chat.user_id)
        ui_settings = (user.settings.ui if user and user.settings else None) or {}
        system_prompt = ui_settings.get('system')
        if system_prompt is None:
            default_interface_settings = await Config.get('ui.default_interface_settings') or {}
            system_prompt = default_interface_settings.get('system')
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})

    return {
        'stream': params.get('stream_response', True),
        'model': model_id,
        'messages': messages,
        'params': params,
        'files': message_meta.get('files') if 'files' in message_meta else chat_data.get('files'),
        'filter_ids': message_meta.get('filter_ids') or None,
        'tool_ids': message_meta.get('tool_ids') or None,
        'skill_ids': message_meta.get('skill_ids') or None,
        'terminal_id': message_meta.get('terminal_id') or None,
        'tool_servers': message_meta.get('tool_servers') or None,
        'features': message_meta.get('features') or {},
        'variables': message_meta.get('variables') or {},
        'chat_variables': chat.variables if (assistant_message.get('user_id') or chat.user_id) == chat.user_id else {},
        'session_id': message_meta.get('session_id'),
        'chat_id': chat_id,
        'id': message_id,
        'parent_id': user_message.get('parentId'),
        'user_message': user_message,
        'assistant_message_id': message_id,
    }
