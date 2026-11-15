"""Pure chat message graph mutations shared by chat persistence operations."""

import time


def _add_child_id_to_parent(messages: dict, parent_id: str | None, child_id: str) -> bool:
    parent = messages.get(parent_id) if parent_id else None
    if not isinstance(parent, dict):
        return False

    child_ids = parent.get('childrenIds')
    if not isinstance(child_ids, list):
        child_ids = []
        parent['childrenIds'] = child_ids

    if child_id in child_ids:
        return False

    child_ids.append(child_id)
    return True


def get_unresolved_parent_ids(messages_map: dict) -> set[str]:
    """Return parent IDs referenced by messages but absent from the map.

    An empty set means the message graph is fully connected.
    """
    return {
        msg['parentId']
        for msg in messages_map.values()
        if msg.get('parentId') and msg['parentId'] not in messages_map
    }


def merge_history(existing_history: dict | None, incoming_history: dict | None) -> dict:
    existing = (existing_history or {}).get('messages') or {}
    incoming = (incoming_history or {}).get('messages') or {}
    merged = {
        message_id: {**message, 'childrenIds': []}
        for message_id, message in {**existing, **incoming}.items()
        if isinstance(message, dict)
    }

    for message_id, message in merged.items():
        parent_id = message.get('parentId')
        if parent_id in merged:
            merged[parent_id]['childrenIds'].append(message_id)

    current_id = (incoming_history or {}).get('currentId')
    if current_id not in merged:
        current_id = (existing_history or {}).get('currentId')
        if current_id not in merged:
            current_id = None

    return {**(existing_history or {}), **(incoming_history or {}), 'messages': merged, 'currentId': current_id}


def delete_message_from_history(history: dict, message_id: str) -> set[str]:
    messages = history.get('messages') or {}
    message = messages.get(message_id)
    if not isinstance(message, dict):
        return set()

    parent_id = message.get('parentId')
    child_ids = [child_id for child_id in (message.get('childrenIds') or []) if child_id in messages]
    grandchild_ids = [
        grandchild_id
        for child_id in child_ids
        for grandchild_id in (messages.get(child_id, {}).get('childrenIds') or [])
        if grandchild_id in messages
    ]

    if parent_id in messages:
        messages[parent_id]['childrenIds'] = [
            child_id for child_id in (messages[parent_id].get('childrenIds') or []) if child_id != message_id
        ] + grandchild_ids

    for grandchild_id in grandchild_ids:
        messages[grandchild_id]['parentId'] = parent_id

    deleted_ids = {message_id, *child_ids}
    for deleted_id in deleted_ids:
        messages.pop(deleted_id, None)

    current_id = parent_id
    child_ids = (
        [child_id for child_id, child in messages.items() if child.get('parentId') is None]
        if current_id is None
        else messages.get(current_id, {}).get('childrenIds', [])
    )
    visited_ids = set()
    while child_ids and child_ids[-1] not in visited_ids:
        current_id = child_ids[-1]
        visited_ids.add(current_id)
        child_ids = messages.get(current_id, {}).get('childrenIds', [])
    history['currentId'] = current_id if current_id in messages else None
    return deleted_ids


def upsert_message_to_history(history: dict, message_id: str, message: dict) -> dict:
    messages = history.setdefault('messages', {})

    if message_id in messages:
        # Voice updates must not replace approval-resume metadata, and approval
        # pauses must retain speech and response timing already attached to this turn.
        existing_meta = messages[message_id].get('meta')
        existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
        incoming_meta = message.get('meta')
        if isinstance(incoming_meta, dict):
            if set(incoming_meta) <= {'voice', 'task_id', 'response_started_at', 'response_ended_at'}:
                message = {**message, 'meta': {**existing_meta, **incoming_meta}}
            else:
                message = {
                    **message,
                    'meta': {
                        **{
                            key: existing_meta[key]
                            for key in ('voice', 'task_id', 'response_started_at', 'response_ended_at')
                            if key in existing_meta
                        },
                        **incoming_meta,
                    },
                }
        messages[message_id] = {
            **messages[message_id],
            **message,
        }
    else:
        message_parent_id = message.get('parentId')
        parent_id = message_parent_id
        if parent_id is None:
            for existing_id, existing_message in messages.items():
                if message_id in existing_message.get('childrenIds', []):
                    parent_id = existing_id
                    break

        parent = messages.get(parent_id) if parent_id else None
        output = message.get('output') or []
        output_role = next(
            (item.get('role') for item in output if isinstance(item, dict) and item.get('role')),
            None,
        )
        role = message.get('role') or output_role
        if not role:
            parent_role = parent.get('role') if parent else None
            if parent_role == 'user':
                role = 'assistant'
            elif parent_role == 'assistant':
                role = 'user'
            else:
                role = 'assistant'

        messages[message_id] = {
            **message,
            'id': message.get('id') or message_id,
            'parentId': message_parent_id if message_parent_id is not None else parent_id,
            'childrenIds': (message.get('childrenIds') if isinstance(message.get('childrenIds'), list) else []),
            'role': role,
            'timestamp': message.get('timestamp') or int(time.time()),
        }
        history['currentId'] = message_id

    _add_child_id_to_parent(messages, messages[message_id].get('parentId'), message_id)
    return messages[message_id]
