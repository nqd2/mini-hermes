from unittest.mock import AsyncMock

import pytest

from open_webui.utils.chat_runtime.resource_context_preparation import ResourceContextAdapters, prepare_resource_context


@pytest.mark.asyncio
async def test_prepare_resource_context_uses_legacy_retrieval_when_file_context_is_enabled():
    original_form = {'messages': [], 'metadata': {'keep': True}}
    contextual_form = {'messages': [{'role': 'system', 'content': 'legacy context'}]}
    legacy_source = {'source': {'name': 'legacy-file'}, 'document': ['content']}
    read_evidence = AsyncMock()
    legacy_handler = AsyncMock(return_value=(contextual_form, {'sources': [legacy_source]}))
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=legacy_handler,
        resource_aware_file_ids=lambda files: [],
        has_resource_file_tools=lambda _tools: False,
        read_resource_evidence=read_evidence,
    )
    sources = [{'source': {'name': 'existing'}, 'document': ['prior']}]

    result, metadata, result_sources = await prepare_resource_context(
        object(),
        original_form,
        {},
        object(),
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        {},
        'question',
        sources,
        adapters,
    )

    assert result is contextual_form
    assert result is not original_form
    assert metadata == {}
    assert result_sources == [sources[0], legacy_source]
    legacy_handler.assert_awaited_once()
    read_evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_resource_context_keeps_attached_knowledge_and_reads_scoped_files():
    request = object()
    user = type('User', (), {'id': 'alice'})()
    uploaded_file = {'type': 'file', 'id': 'upload-1', 'name': 'report.xlsx'}
    attached_knowledge = {'type': 'collection', 'id': 'knowledge-1'}
    metadata = {
        'resource_aware_files': True,
        'resource_model_tools': True,
        'resource_files': [uploaded_file],
        'files': [uploaded_file, attached_knowledge],
        'tools': {'view_file': {'spec': {}}},
    }
    form_data = {'messages': [], 'tools': [{'type': 'function'}]}
    knowledge_source = {'source': {'name': 'knowledge'}, 'document': ['knowledge evidence']}
    resource_source = {'source': {'name': 'report.xlsx'}, 'document': ['bounded evidence']}
    legacy_handler = AsyncMock(return_value=({}, {'sources': [knowledge_source]}))
    read_evidence = AsyncMock(return_value=[resource_source])
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=legacy_handler,
        resource_aware_file_ids=lambda files: [item['id'] for item in files or []],
        has_resource_file_tools=lambda tools: 'view_file' in tools,
        read_resource_evidence=read_evidence,
    )
    sources = []

    result, result_metadata, result_sources = await prepare_resource_context(
        request,
        form_data,
        {'__metadata__': metadata},
        user,
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        metadata,
        'What is in the report?',
        sources,
        adapters,
    )

    assert result is form_data
    assert result_metadata['resource_file_tools_available'] is True
    assert form_data['tools'] == [{'type': 'function'}]
    assert result_sources == [knowledge_source, resource_source]
    legacy_form = legacy_handler.await_args.args[1]
    assert legacy_form is not form_data
    assert legacy_form['metadata']['files'] == [attached_knowledge]
    assert metadata['files'] == [uploaded_file, attached_knowledge]
    read_evidence.assert_awaited_once_with(user.id, metadata, 'What is in the report?')


@pytest.mark.asyncio
async def test_prepare_resource_context_removes_tool_fields_when_model_has_no_resource_tools():
    metadata = {
        'resource_aware_files': True,
        'resource_model_tools': False,
        'resource_files': [],
        'tools': {},
    }
    form_data = {'messages': [], 'tools': [{'type': 'function'}], 'tool_choice': 'auto'}
    read_evidence = AsyncMock(return_value=[])
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=AsyncMock(),
        resource_aware_file_ids=lambda _files: [],
        has_resource_file_tools=lambda _tools: pytest.fail('must not inspect tools when capability is disabled'),
        read_resource_evidence=read_evidence,
    )

    result, result_metadata, sources = await prepare_resource_context(
        object(),
        form_data,
        {},
        type('User', (), {'id': 'alice'})(),
        {},
        metadata,
        'question',
        [],
        adapters,
    )

    assert result is form_data
    assert result_metadata['resource_file_tools_available'] is False
    assert 'tools' not in form_data
    assert 'tool_choice' not in form_data
    assert sources == []
    read_evidence.assert_awaited_once()


@pytest.mark.asyncio
async def test_large_full_summary_requires_document_task_instead_of_finishing_on_partial_excerpt():
    metadata = {
        'resource_aware_files': True,
        'resource_model_tools': True,
        'resource_execution_requested': True,
        'resource_context_mode': 'HIERARCHICAL',
        'resource_scope_ids': ['upload-1'],
        'agent_task_state': {
            'required_action': 'read',
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'status': 'in_progress',
        },
        'resource_files': [{'type': 'file', 'id': 'upload-1', 'name': 'M.xlsx'}],
        'tools': {
            name: {'spec': {'name': name}}
            for name in (
                'list_editable_documents',
                'start_document_summary_task',
                'get_document_task',
                'read_editable_document',
                'search_editable_document',
            )
        },
    }
    form_data = {
        'messages': [],
        'tools': [
            {'type': 'function', 'function': {'name': name}}
            for name in metadata['tools']
        ],
        'tool_choice': 'auto',
    }
    partial_source = {
        'source': {'id': 'upload-1', 'name': 'M.xlsx', 'type': 'file'},
        'document': ['bounded excerpt'],
        'metadata': [{'evidence_id': 'evidence-1', 'coverage_complete': False}],
    }
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=AsyncMock(),
        resource_aware_file_ids=lambda _files: ['upload-1'],
        has_resource_file_tools=lambda _tools: True,
        read_resource_evidence=AsyncMock(return_value=[partial_source]),
    )

    await prepare_resource_context(
        object(),
        form_data,
        {},
        type('User', (), {'id': 'alice'})(),
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        metadata,
        'tóm tắt file này đi',
        [],
        adapters,
    )

    assert form_data['tool_choice'] == {
        'type': 'function',
        'function': {'name': 'list_editable_documents'},
    }
    assert metadata['resource_summary_task_phase'] == 'list'


@pytest.mark.asyncio
async def test_full_summary_resume_forces_status_lookup_for_saved_task():
    metadata = {
        'resource_aware_files': True,
        'resource_model_tools': True,
        'resource_execution_requested': True,
        'resource_context_mode': 'HIERARCHICAL',
        'resource_scope_ids': ['upload-1'],
        'pending_resource_task_ids': ['task-1'],
        'agent_task_state': {
            'required_action': 'read',
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'status': 'pending',
            'resource_task_ids': ['task-1'],
        },
        'resource_files': [{'type': 'file', 'id': 'upload-1', 'name': 'M.xlsx'}],
        'tools': {
            name: {'spec': {'name': name}}
            for name in ('list_editable_documents', 'start_document_summary_task', 'get_document_task')
        },
    }
    form_data = {
        'messages': [],
        'tools': [
            {'type': 'function', 'function': {'name': name}}
            for name in metadata['tools']
        ],
    }
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=AsyncMock(),
        resource_aware_file_ids=lambda _files: ['upload-1'],
        has_resource_file_tools=lambda _tools: True,
        read_resource_evidence=AsyncMock(return_value=[]),
    )

    await prepare_resource_context(
        object(),
        form_data,
        {},
        type('User', (), {'id': 'alice'})(),
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        metadata,
        'Continue the summary',
        [],
        adapters,
    )

    assert form_data['tool_choice'] == {
        'type': 'function',
        'function': {'name': 'get_document_task'},
    }
    assert metadata['resource_summary_task_phase'] == 'status'


@pytest.mark.asyncio
async def test_full_summary_resume_restarts_after_index_pending_without_summary_task_id():
    metadata = {
        'resource_aware_files': True,
        'resource_model_tools': True,
        'resource_execution_requested': True,
        'resource_context_mode': 'HIERARCHICAL',
        'resource_scope_ids': ['upload-1'],
        'agent_task_state': {
            'required_action': 'read',
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'status': 'pending',
            'reason': 'resource_index_pending',
            'resource_task_ids': [],
            'resource_index_job_ids': ['index-1'],
        },
        'resource_files': [{'type': 'file', 'id': 'upload-1', 'name': 'M.xlsx'}],
        'tools': {
            name: {'spec': {'name': name}}
            for name in ('list_editable_documents', 'start_document_summary_task', 'get_document_task')
        },
    }
    form_data = {
        'messages': [],
        'tools': [
            {'type': 'function', 'function': {'name': name}}
            for name in metadata['tools']
        ],
    }
    adapters = ResourceContextAdapters(
        chat_completion_files_handler=AsyncMock(),
        resource_aware_file_ids=lambda _files: ['upload-1'],
        has_resource_file_tools=lambda _tools: True,
        read_resource_evidence=AsyncMock(return_value=[]),
    )

    await prepare_resource_context(
        object(),
        form_data,
        {},
        type('User', (), {'id': 'alice'})(),
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        metadata,
        'Continue the summary',
        [],
        adapters,
    )

    assert form_data['tool_choice'] == {
        'type': 'function',
        'function': {'name': 'list_editable_documents'},
    }
    assert metadata['resource_summary_task_phase'] == 'list'
