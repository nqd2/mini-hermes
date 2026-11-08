"""Post-turn task orchestration for completed chat responses."""

import re
from typing import Any

from open_webui.constants import TASKS
from open_webui.models.chats import Chats
from open_webui.routers.tasks import generate_chat_tags, generate_follow_ups, generate_title
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.memory import review_memory_after_turn
from open_webui.utils.misc import (
    get_content_from_message,
    get_last_user_message,
    get_last_user_message_item,
    get_message_list,
)
from open_webui.utils.skills import replace_skill_mentions_with_labels
from open_webui.utils.task import parse_follow_ups


async def _load_conversation(ctx: dict[str, Any]) -> tuple[dict | None, list[dict], dict | None]:
    metadata = ctx['metadata']
    form_data = ctx['form_data']

    if is_saved_chat_id(metadata.get('chat_id')):
        messages_map = await Chats.get_messages_map_by_chat_id(metadata['chat_id'])
        if not messages_map:
            return None, [], None

        message = messages_map.get(metadata['message_id'])
        messages = []
        for message in get_message_list(messages_map, metadata['message_id']):
            content = message.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(
                    item['text']
                    for item in content
                    if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)
                )
            if isinstance(content, str):
                content = re.sub(
                    r'<details\b[^<>]*>(?:(?!<details\b).)*?<\/details>|!\[[^\[\]]*\]\([^()]*\)',
                    '',
                    content,
                    flags=re.S | re.I,
                ).strip()
            messages.append({**message, 'role': message.get('role', 'assistant'), 'content': content})

        return message, messages, messages_map

    message = get_last_user_message_item(form_data.get('messages', []))
    messages = form_data.get('messages', [])
    if message:
        message = {**message, 'model': form_data.get('model')}
    return message, messages, None


def _follow_up_text(result: dict[str, Any]) -> str:
    choices = result.get('choices', [])
    if len(choices) != 1:
        return ''
    response_message = choices[0].get('message', {})
    return response_message.get('content') or response_message.get('reasoning_content', '')


async def _generate_follow_ups(ctx: dict[str, Any], message: dict[str, Any], messages: list[dict]) -> None:
    metadata = ctx['metadata']
    follow_up_messages = list(messages)
    assistant_message = ctx.get('assistant_message')
    if not is_saved_chat_id(metadata.get('chat_id')) and assistant_message:
        if follow_up_messages[-1].get('role') == 'assistant' and (
            follow_up_messages[-1].get('id') == metadata['message_id']
            or get_content_from_message(follow_up_messages[-1]) == get_content_from_message(assistant_message)
        ):
            follow_up_messages.pop()
        follow_up_messages.append({**assistant_message, 'role': 'assistant', 'id': metadata['message_id']})

    result = await generate_follow_ups(
        ctx['request'],
        {
            'model': message['model'],
            'messages': follow_up_messages,
            'message_id': metadata['message_id'],
            'chat_id': metadata['chat_id'],
        },
        ctx['user'],
    )
    if not result or not isinstance(result, dict):
        return

    try:
        follow_ups = parse_follow_ups(_follow_up_text(result))
        await ctx['event_emitter']({'type': 'chat:message:follow_ups', 'data': {'follow_ups': follow_ups}})
        if is_saved_chat_id(metadata.get('chat_id')):
            await Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata['chat_id'],
                metadata['message_id'],
                {'followUps': follow_ups},
                touch=False,
            )
    except Exception:
        pass


def _parse_generated_title(result: dict[str, Any] | None, fallback: str | None) -> str:
    if not result or not isinstance(result, dict):
        return ''
    choices = result.get('choices', [])
    if len(choices) != 1:
        return ''
    response_message = choices[0].get('message', {})
    title_string = response_message.get('content') or response_message.get('reasoning_content') or fallback or ''
    title_string = title_string[title_string.find('{') : title_string.rfind('}') + 1]
    try:
        return JSONCodec.loads(title_string).get('title', fallback) or ''
    except Exception:
        return ''


async def _update_chat_title(
    ctx: dict[str, Any], message: dict[str, Any], messages: list[dict], messages_map: dict | None
) -> None:
    metadata = ctx['metadata']
    tasks = ctx['tasks']
    user_message = get_last_user_message(messages)
    if user_message and len(user_message) > 100:
        user_message = user_message[:100] + '...'

    title = None
    if tasks.get(TASKS.TITLE_GENERATION):
        result = await generate_title(
            ctx['request'],
            {'model': message['model'], 'messages': messages, 'chat_id': metadata['chat_id']},
            ctx['user'],
        )
        if result and isinstance(result, dict):
            title = _parse_generated_title(result, message.get('content', user_message))
            if not title:
                title = replace_skill_mentions_with_labels(messages[0].get('content', user_message))
            await Chats.update_chat_title_by_id(metadata['chat_id'], title)
            await ctx['event_emitter']({'type': 'chat:title', 'data': title})

    if title is None and len(messages) == 2 and (not messages_map or len(messages_map) <= 2):
        title = replace_skill_mentions_with_labels(messages[0].get('content', user_message))
        await Chats.update_chat_title_by_id(metadata['chat_id'], title)
        await ctx['event_emitter']({'type': 'chat:title', 'data': title})


async def _update_chat_tags(ctx: dict[str, Any], message: dict[str, Any], messages: list[dict]) -> None:
    if not ctx['tasks'].get(TASKS.TAGS_GENERATION):
        return

    metadata = ctx['metadata']
    result = await generate_chat_tags(
        ctx['request'],
        {'model': message['model'], 'messages': messages, 'chat_id': metadata['chat_id']},
        ctx['user'],
    )
    if not result or not isinstance(result, dict):
        return

    choices = result.get('choices', [])
    if len(choices) == 1:
        response_message = choices[0].get('message', {})
        tags_string = response_message.get('content') or response_message.get('reasoning_content', '')
    else:
        tags_string = ''
    tags_string = tags_string[tags_string.find('{') : tags_string.rfind('}') + 1]

    try:
        tags = JSONCodec.loads(tags_string).get('tags', [])
        await Chats.update_chat_tags_by_id(metadata['chat_id'], tags, ctx['user'])
        await ctx['event_emitter']({'type': 'chat:tags', 'data': tags})
    except Exception:
        pass


async def _review_memory(ctx: dict[str, Any], messages: list[dict]) -> None:
    if not messages:
        return
    await review_memory_after_turn(
        request=ctx['request'],
        user=ctx['user'],
        model=ctx.get('model'),
        metadata=ctx['metadata'],
        form_data=ctx['form_data'],
        assistant_message=ctx.get('assistant_message') or {},
        messages=messages,
    )


async def background_tasks_handler(ctx: dict[str, Any]) -> None:
    """Run enabled post-turn tasks and persist their results for a chat response."""
    message, messages, messages_map = await _load_conversation(ctx)
    if is_saved_chat_id(ctx['metadata'].get('chat_id')) and messages_map is None:
        return
    if not message or 'model' not in message:
        return

    if ctx['tasks'] and messages:
        if ctx['tasks'].get(TASKS.FOLLOW_UP_GENERATION):
            await _generate_follow_ups(ctx, message, messages)

        if is_saved_chat_id(ctx['metadata'].get('chat_id')):
            if TASKS.TITLE_GENERATION in ctx['tasks']:
                await _update_chat_title(ctx, message, messages, messages_map)
            await _update_chat_tags(ctx, message, messages)

    await _review_memory(ctx, messages)
