import pytest

from open_webui.mini_hermes.model_capabilities import ModelCapabilities


@pytest.mark.asyncio
async def test_new_file_request_resolves_state_before_read_and_answer():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    calls = []

    async def search(user_id, files, chat_id, query, **kwargs):
        calls.append((user_id, files, query, kwargs))
        return [
            {
                'source': {'id': 'file-1'},
                'document': ['actual content'],
                'metadata': [{'revision': 1, 'evidence_id': 'chunk-1', 'coverage_complete': True}],
            }
        ]

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(metadata, 'tóm tắt', ModelCapabilities(), pending_task_ids=[])
    evidence = await pipeline.read('alice', metadata, 'tóm tắt')
    assert evidence
    assert calls[0][0] == 'alice'
    assert calls[0][3]['bounded_initial_read'] is True
    assert metadata['agent_task_state']['evidence_ids'] == ['chunk-1']
    assert pipeline.decision(metadata).status == 'completed'


@pytest.mark.asyncio
async def test_backend_search_satisfies_a_search_request_without_a_second_tool_call():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def search(*args, **kwargs):
        return [
            {
                'source': {'id': 'file-1'},
                'document': ['A tuition amount is listed.'],
                'metadata': [{'revision': 1, 'evidence_id': 'chunk-1', 'coverage_complete': False}],
            }
        ]

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(metadata, 'Find the tuition amount in this file', ModelCapabilities(), pending_task_ids=[])
    await pipeline.read('alice', metadata, 'Find the tuition amount in this file')
    assert pipeline.decision(metadata).status == 'completed'


@pytest.mark.asyncio
async def test_greeting_does_not_read_files_and_does_not_gate_normal_answer():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def forbidden(*args, **kwargs):
        pytest.fail('Greeting must not read files')

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(forbidden)
    pipeline.resolve(metadata, 'hi', ModelCapabilities(), pending_task_ids=[])
    assert await pipeline.read('alice', metadata, 'hi') == []
    assert pipeline.decision(metadata).status == 'completed'


@pytest.mark.asyncio
async def test_read_failure_is_explicit_and_cannot_release_unverified_answer():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def search(*args, **kwargs):
        raise PermissionError('access revoked')

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(metadata, 'What is in this file?', ModelCapabilities(), pending_task_ids=[])
    assert await pipeline.read('alice', metadata, 'What is in this file?') == []
    assert pipeline.decision(metadata).status == 'blocked'
    assert metadata['agent_task_state']['evidence_ids'] == []


@pytest.mark.asyncio
async def test_indexing_pending_is_reported_as_pending_instead_of_missing_file():
    from open_webui.mini_hermes.execution.contracts import ResourceIndexPending
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def search(*args, **kwargs):
        raise ResourceIndexPending(['index-job-1'])

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(metadata, 'What is in this file?', ModelCapabilities(), pending_task_ids=[])

    assert await pipeline.read('alice', metadata, 'What is in this file?') == []
    assert pipeline.decision(metadata).status == 'pending'
    assert metadata['agent_task_state']['reason'] == 'resource_index_pending'
    assert metadata['agent_task_state']['index_job_ids'] == ['index-job-1']


@pytest.mark.asyncio
async def test_partial_read_cannot_complete_whole_document_summary():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def search(*args, **kwargs):
        return [
            {
                'source': {'id': 'file-1'},
                'document': ['partial content'],
                'metadata': [{'revision': 1, 'evidence_id': 'chunk-1', 'coverage_complete': False}],
            }
        ]

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(metadata, 'Summarize this file', ModelCapabilities(), pending_task_ids=[])
    await pipeline.read('alice', metadata, 'Summarize this file')
    assert pipeline.decision(metadata).status == 'partial'


@pytest.mark.asyncio
async def test_full_coverage_of_one_file_does_not_complete_a_multi_file_summary():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def search(*args, **kwargs):
        return [
            {
                'source': {'id': 'file-1'},
                'document': ['Full content of the first file.'],
                'metadata': [
                    {
                        'file_id': 'file-1',
                        'revision': 2,
                        'evidence_id': 'chunk-1',
                        'coverage_complete': True,
                    }
                ],
            }
        ]

    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'revision': 2},
            {'type': 'file', 'id': 'file-2', 'revision': 5},
        ]
    }
    pipeline = ResourceRequestPipeline(search)
    pipeline.resolve(
        metadata,
        'Summarize all attached documents',
        ModelCapabilities(),
        pending_task_ids=[],
    )
    await pipeline.read('alice', metadata, 'Summarize all attached documents')

    assert metadata['agent_task_state']['covered_resource_versions'] == {'file-1': 2}
    assert metadata['agent_task_state']['coverage_complete'] is False
    assert pipeline.decision(metadata).status == 'partial'


def test_resume_invalidates_coverage_when_the_resource_revision_changes():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = {
        'goal': 'Summarize this file',
        'required_action': 'read',
        'required_actions': ['read'],
        'status': 'in_progress',
        'steps': [
            {
                'tool': 'read_editable_document',
                'action': 'read',
                'status': 'succeeded',
                'evidence_ids': ['old-revision-evidence'],
                'has_content': True,
            }
        ],
        'resource_scope_ids': ['file-1'],
        'resource_scope_versions': {'file-1': 1},
        'covered_resource_versions': {'file-1': 1},
        'coverage_complete': True,
        'requires_complete_coverage': True,
        'evidence_ids': ['old-revision-evidence'],
    }
    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'revision': 2},
        ],
        'agent_task_state': state,
    }

    ResourceRequestPipeline().resolve(metadata, 'Continue', ModelCapabilities(), pending_task_ids=[])

    assert state['covered_resource_versions'] == {}
    assert state['coverage_complete'] is False
    assert state['steps'] == []
    assert state['evidence_ids'] == []
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'


def test_blocked_checkpoint_cannot_release_an_answer_with_old_revision_evidence():
    from open_webui.mini_hermes.agent_execution import record_tool_result
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = {
        'goal': 'What is in this file?',
        'required_action': 'read',
        'required_actions': ['read'],
        'status': 'blocked',
        'steps': [
            {
                'tool': 'read_editable_document',
                'action': 'read',
                'status': 'succeeded',
                'evidence_ids': ['revision-1-evidence'],
                'has_content': True,
            }
        ],
        'resource_scope_ids': ['file-1'],
        'resource_scope_versions': {'file-1': 1},
        'covered_resource_versions': {'file-1': 1},
        'coverage_complete': False,
        'evidence_ids': ['revision-1-evidence'],
    }
    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'revision': 2},
        ],
        'agent_task_state': state,
    }
    pipeline = ResourceRequestPipeline()

    pipeline.resolve(metadata, 'Continue', ModelCapabilities(), pending_task_ids=[])
    record_tool_result(state, 'list_editable_documents', [{'id': 'file-1', 'filename': 'report.txt'}])

    assert state['evidence_ids'] == []
    assert state['steps'] == [
        {
            'tool': 'list_editable_documents',
            'action': 'discover',
            'status': 'succeeded',
            'evidence_ids': [],
            'has_content': False,
            'effect_confirmed': True,
        }
    ]
    assert pipeline.decision(metadata).status == 'partial'


def test_follow_up_reference_keeps_task_scope_and_greeting_does_not():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    metadata = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline = ResourceRequestPipeline()
    pipeline.resolve(metadata, 'Còn file thứ hai thì sao?', ModelCapabilities(), pending_task_ids=[])
    assert metadata['resource_execution_requested'] is True
    assert metadata['resource_evidence_requested'] is True

    greeting = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline.resolve(greeting, 'hi', ModelCapabilities(), pending_task_ids=[])
    assert greeting['resource_execution_requested'] is False

    resumed = {'resource_history_files': [{'type': 'file', 'id': 'file-1'}]}
    pipeline.resolve(resumed, 'hi', ModelCapabilities(), pending_task_ids=['task-1'])
    assert resumed['resource_greeting'] is True
    assert resumed['resource_execution_requested'] is False


@pytest.mark.asyncio
async def test_greeting_does_not_resume_a_pending_background_resource_task():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def forbidden(*_args, **_kwargs):
        pytest.fail('A greeting must not read resources, even when a background task is pending')

    metadata = {
        'resource_history_files': [{'type': 'file', 'id': 'file-1', 'name': 'report.pdf'}],
        'agent_task_state': {
            'goal': 'Summarize this file',
            'required_action': 'read',
            'required_actions': ['read'],
            'status': 'pending',
            'steps': [],
            'resource_scope_ids': ['file-1'],
            'evidence_ids': [],
        },
    }
    pipeline = ResourceRequestPipeline(forbidden)

    pipeline.resolve(metadata, 'hi', ModelCapabilities(), pending_task_ids=['summary-task-1'])

    assert metadata['resource_greeting'] is True
    assert metadata['resource_execution_requested'] is False
    assert await pipeline.read('alice', metadata, 'hi') == []


def test_toolless_resource_discovery_uses_bounded_metadata_without_reading_content():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'name': 'report.xlsx', 'revision': 2},
        ]
    }
    pipeline = ResourceRequestPipeline()
    pipeline.resolve(metadata, 'List the files attached to this chat', ModelCapabilities(), pending_task_ids=[])

    assert metadata['resource_execution_requested'] is True
    assert metadata['resource_evidence_requested'] is False
    assert 'report.xlsx' in metadata['resource_manifest_context']
    assert pipeline.decision(metadata).status == 'completed'


def test_resuming_a_full_summary_preserves_coverage_requirement():
    from open_webui.mini_hermes.agent_execution import initialize_task_state, record_tool_result
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = initialize_task_state('Summarize this file')
    state['requires_complete_coverage'] = True
    record_tool_result(
        state,
        'search_editable_document',
        {
            'status': 'succeeded',
            'coverage_complete': False,
            'results': [{'evidence_id': 'ev-1', 'text': 'partial section'}],
        },
    )
    state['coverage_complete'] = False
    metadata = {
        'resource_history_files': [{'type': 'file', 'id': 'file-1', 'name': 'report.pdf'}],
        'agent_task_state': state,
    }

    ResourceRequestPipeline().resolve(metadata, 'Continue', ModelCapabilities(), pending_task_ids=[])

    assert state['requires_complete_coverage'] is True
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'


def test_resume_with_a_revoked_resource_scope_cannot_reuse_old_evidence():
    from open_webui.mini_hermes.agent_execution import initialize_task_state, record_tool_result
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = initialize_task_state('What is in this file?')
    state['resource_scope_ids'] = ['file-1']
    record_tool_result(
        state,
        'read_editable_document',
        {
            'status': 'succeeded',
            'evidence_id': 'ev-1',
            'text': 'old content',
        },
    )
    metadata = {
        'resource_history_files': [{'type': 'file', 'id': 'file-2', 'name': 'other.pdf'}],
        'agent_task_state': state,
    }

    ResourceRequestPipeline().resolve(metadata, 'Continue', ModelCapabilities(), pending_task_ids=[])

    assert ResourceRequestPipeline.decision(metadata).status == 'blocked'
    assert metadata['agent_task_state']['reason'] == 'resource_scope_changed'


def test_pending_checkpoint_without_tool_steps_keeps_its_goal_and_resource_scope():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = {
        'goal': 'Find the tuition amount in the second file',
        'required_action': 'search',
        'required_actions': ['search'],
        'status': 'pending',
        'steps': [],
        'resource_scope_ids': ['file-2'],
        'evidence_ids': [],
    }
    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'name': 'first.xlsx'},
            {'type': 'file', 'id': 'file-2', 'name': 'second.xlsx'},
        ],
        'agent_task_state': state,
    }

    ResourceRequestPipeline().resolve(metadata, 'Continue', ModelCapabilities(), pending_task_ids=[])

    assert metadata['resource_task_prompt'] == state['goal']
    assert metadata['resource_scope_ids'] == ['file-2']
    assert metadata['resource_evidence_requested'] is True


def test_display_requirement_is_not_satisfied_by_resource_existence():
    from open_webui.mini_hermes.agent_execution import record_tool_result
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    metadata = {
        'resource_aware_files': True,
        'resource_execution_requested': True,
        'agent_task_state': {'required_actions': ['display'], 'steps': [], 'evidence_ids': []},
    }
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'
    record_tool_result(metadata['agent_task_state'], 'display_file', {'exists': True, 'displayed': True})
    assert ResourceRequestPipeline.decision(metadata).status == 'completed'


@pytest.mark.asyncio
async def test_ambiguous_singular_reference_does_not_read_arbitrary_file():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    async def forbidden(*args, **kwargs):
        pytest.fail('Ambiguous scope must be clarified before reading')

    metadata = {
        'resource_history_files': [
            {'type': 'file', 'id': 'file-1', 'name': 'one.xlsx'},
            {'type': 'file', 'id': 'file-2', 'name': 'two.pdf'},
        ]
    }
    pipeline = ResourceRequestPipeline(forbidden)
    pipeline.resolve(metadata, 'What is in this file?', ModelCapabilities(), pending_task_ids=[])
    assert await pipeline.read('alice', metadata, 'What is in this file?') == []
    assert metadata['agent_task_state']['reason'] == 'ambiguous_resource_scope'
    assert pipeline.decision(metadata).status == 'pending'
