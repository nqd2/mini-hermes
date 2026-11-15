from open_webui.models import chat_history_mutations
from open_webui.models.chats import ChatTable


def test_message_history_mutations_preserve_branch_and_metadata():
    history = {'messages': {'user': {
        'id': 'user', 'role': 'user', 'parentId': None, 'childrenIds': [], 'content': 'request',
    }}, 'currentId': 'user'}

    assistant = ChatTable.upsert_message_to_history(history, 'assistant', {
        'parentId': 'user', 'role': 'assistant', 'content': 'answer', 'meta': {'voice': {'id': 'audio'}},
    })
    assert assistant == history['messages']['assistant']
    assert history['messages']['user']['childrenIds'] == ['assistant']
    assert chat_history_mutations.upsert_message_to_history is not ChatTable.upsert_message_to_history

    ChatTable.upsert_message_to_history(history, 'assistant', {'meta': {'task_id': 'task-1'}})
    assert history['messages']['assistant']['meta'] == {'voice': {'id': 'audio'}, 'task_id': 'task-1'}
    assert ChatTable.delete_message_from_history(history, 'assistant') == {'assistant'}
    assert history['currentId'] == 'user'
