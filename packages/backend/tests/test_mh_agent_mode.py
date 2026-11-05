from open_webui.mini_hermes.agent_mode import document_agent_mode


def user(text):
    return {'role': 'user', 'content': text}


def test_document_request_activates_without_command():
    assert document_agent_mode([user('Sửa tên trong file báo cáo này')])
    assert document_agent_mode([user('Change the name to X')], has_files=True)
    assert not document_agent_mode([user('Hello')])


def test_explicit_commands_persist_and_override_auto_detection():
    assert document_agent_mode([user('/tool'), user('Hello')])
    assert not document_agent_mode([user('/tool'), user('/stop-tool'), user('Sửa file này')], has_files=True)
    assert document_agent_mode([user('/stop-tool'), user('Please /tool edit this file')])
    assert not document_agent_mode([user('edit this file /tool /stop-tool')])
