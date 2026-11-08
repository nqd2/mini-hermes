from open_webui.utils.chat_runtime.resource_context import resolve_chat_resource_context


def test_collects_and_deduplicates_file_references_from_request_history_and_manifest(monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'true')
    result = resolve_chat_resource_context(
        messages=[
            {'role': 'user', 'files': [{'id': 'history', 'name': 'history.txt'}]},
            {'role': 'assistant', 'files': [{'id': 'ignored-role'}]},
        ],
        request_metadata={
            'files': [
                {'id': 'request', 'name': 'request-old.txt'},
                {'id': 'legacy', 'legacy': True},
                {'id': 'image', 'type': 'image'},
            ],
            'user_message': {'files': [{'id': 'user-message', 'name': 'message.txt'}]},
        },
        request_files=[{'id': 'form', 'name': 'form.txt'}],
        resource_manifest=[
            {'id': 'request', 'name': 'verified.txt', 'revision': 3},
            {'id': 'manifest', 'name': 'manifest.txt'},
        ],
        base_include_file_context=False,
    )

    assert [item['id'] for item in result.history_files] == ['request', 'form', 'user-message', 'history', 'manifest']
    assert result.history_files[0] == {
        'id': 'request',
        'name': 'verified.txt',
        'revision': 3,
    }
    assert result.has_scoped_documents is True
    assert result.has_files is True
    assert result.include_file_context is True


def test_edit_words_without_resource_scope_do_not_activate_agent_mode(monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'true')
    result = resolve_chat_resource_context(
        messages=[{'role': 'user', 'content': 'edit the contents'}],
        request_metadata={},
        request_files=None,
        resource_manifest=[],
        base_include_file_context=False,
    )

    assert result.history_files == []
    assert result.has_files is False
    assert result.agent_enabled is False


def test_chat_manifest_file_enables_implicit_document_task_but_not_greeting(monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'true')
    manifest = [{'id': 'file-1', 'name': 'report.xlsx', 'type': 'file'}]

    edit = resolve_chat_resource_context(
        messages=[{'role': 'user', 'content': 'edit the value'}],
        request_metadata={},
        request_files=None,
        resource_manifest=manifest,
        base_include_file_context=False,
    )
    greeting = resolve_chat_resource_context(
        messages=[{'role': 'user', 'content': 'hi'}],
        request_metadata={},
        request_files=None,
        resource_manifest=manifest,
        base_include_file_context=False,
    )

    assert edit.agent_enabled is True
    assert greeting.agent_enabled is False
    assert edit.include_file_context is True


def test_resource_aware_flag_preserves_legacy_context_setting(monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'false')
    result = resolve_chat_resource_context(
        messages=[],
        request_metadata={},
        request_files=[{'id': 'file-1'}],
        resource_manifest=[],
        base_include_file_context=True,
    )

    assert result.has_files is True
    assert result.include_file_context is True
