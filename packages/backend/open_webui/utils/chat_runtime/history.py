"""Persistence adapter for reconstructing model-ready chat history."""

from open_webui.models.chats import Chats
from open_webui.utils.misc import (
    get_content_from_message,
    get_message_list,
    set_last_user_message_content,
)

MESSAGE_REPLAY_KEYS = ('id', 'role', 'content', 'output', 'files', 'contextSummary', 'usage', 'model', 'meta')


async def load_messages_from_db(chat_id: str, message_id: str) -> list[dict] | None:
    """Load one branch from storage and retain only fields used to rebuild the model payload."""
    messages_map = await Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    for message in db_messages:
        skills_create = (message.get('meta') or {}).get('skills_create')
        # Replay the saved expansion unless the user edited the message.
        if skills_create and skills_create.get('original_text') == get_content_from_message(message):
            set_last_user_message_content(skills_create['expanded_text'], [message])

    return [
        {key: value for key, value in message.items() if key in MESSAGE_REPLAY_KEYS}
        for message in db_messages
        if not (message.get('role') == 'assistant' and not message.get('content') and not message.get('output'))
    ]
