from open_webui.mini_hermes.agent_execution import (
    can_answer_task,
    initialize_task_state,
    record_tool_result,
    task_checkpoint,
)


def test_display_success_does_not_satisfy_a_content_request():
    state = initialize_task_state('What is in this file?', required_action='read')

    record_tool_result(state, 'display_file', {'displayed': True, 'name': 'M.xlsx'})

    assert not can_answer_task(state)
    assert state['steps'][0]['action'] == 'display'


def test_reader_evidence_satisfies_content_request_and_keeps_provenance():
    state = initialize_task_state('Summarize this document', required_action='read')

    record_tool_result(
        state,
        'read_editable_document',
        {
            'status': 'ready',
            'revision': 3,
            'evidence_id': 'ev-1',
            'text': 'actual content',
        },
    )

    assert can_answer_task(state)
    assert state['evidence_ids'] == ['ev-1']
    assert state['steps'][0]['revision'] == 3


def test_completed_hierarchical_summary_satisfies_read_and_coverage_requirements():
    state = initialize_task_state('Summarize this document')
    state['requires_complete_coverage'] = True
    state['resource_scope_ids'] = ['upload-1']

    record_tool_result(
        state,
        'get_document_task',
        {
            'status': 'completed',
            'result': 'This document covers tuition and admission requirements.',
            'resource_id': 'upload-1',
            'revision': 3,
            'coverage_complete': True,
            'evidence_ids': ['chunk-1', 'chunk-2'],
        },
    )

    assert can_answer_task(state)
    assert state['coverage_complete'] is True
    assert state['covered_resource_versions'] == {'upload-1': 3}
    assert state['evidence_ids'] == ['chunk-1', 'chunk-2']


def test_one_document_summary_does_not_complete_a_multi_resource_scope():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = initialize_task_state('Summarize all attached documents')
    state['requires_complete_coverage'] = True
    state['resource_scope_ids'] = ['upload-a', 'upload-b']
    state['resource_scope_versions'] = {'upload-a': 1, 'upload-b': 4}

    record_tool_result(
        state,
        'get_document_task',
        {
            'status': 'completed',
            'result': 'Summary of A',
            'resource_id': 'upload-a',
            'revision': 1,
            'coverage_complete': True,
            'evidence_ids': ['a-1'],
        },
    )
    metadata = {
        'resource_aware_files': True,
        'resource_execution_requested': True,
        'agent_task_state': state,
    }

    assert state['coverage_complete'] is False
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'

    record_tool_result(
        state,
        'get_document_task',
        {
            'status': 'completed',
            'result': 'Summary of B',
            'resource_id': 'upload-b',
            'revision': 4,
            'coverage_complete': True,
            'evidence_ids': ['b-1'],
        },
    )

    assert state['coverage_complete'] is True
    assert ResourceRequestPipeline.decision(metadata).status == 'completed'


def test_index_search_completion_does_not_claim_full_document_coverage():
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    state = initialize_task_state('Summarize this file')
    state['requires_complete_coverage'] = True
    state['resource_scope_ids'] = ['upload-1']
    record_tool_result(
        state,
        'search_editable_document',
        {
            'status': 'succeeded',
            'search_complete': True,
            'coverage_complete': False,
            'resource_id': 'upload-1',
            'revision': 1,
            'evidence_ids': ['matching-chunk'],
            'results': [{'evidence_id': 'matching-chunk', 'text': 'Only a matching excerpt'}],
        },
    )
    metadata = {
        'resource_aware_files': True,
        'resource_execution_requested': True,
        'agent_task_state': state,
    }

    assert state['steps'][0]['search_complete'] is True
    assert state['coverage_complete'] is False
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'


def test_cancelled_hierarchical_summary_cannot_satisfy_read():
    state = initialize_task_state('Summarize this document')
    state['requires_complete_coverage'] = True

    record_tool_result(
        state,
        'get_document_task',
        {
            'status': 'cancelled',
            'result': 'Partial result',
            'revision': 3,
            'coverage_complete': False,
            'evidence_ids': ['chunk-1'],
        },
    )

    assert not can_answer_task(state)
    assert state['steps'][0]['status'] == 'blocked'


def test_failed_tool_cannot_mark_task_coverage_complete():
    state = initialize_task_state('Summarize this document')
    state['requires_complete_coverage'] = True

    record_tool_result(
        state,
        'get_document_task',
        {
            'status': 'failed',
            'result': 'Untrusted partial result',
            'resource_id': 'upload-1',
            'revision': 3,
            'coverage_complete': True,
            'evidence_ids': ['chunk-1'],
        },
    )

    assert state.get('coverage_complete') is not True
    assert not can_answer_task(state)


def test_tool_error_does_not_count_as_completed_search():
    state = initialize_task_state('Find the value', required_action='search')

    record_tool_result(state, 'search_editable_document', {'error': 'permission denied'})

    assert not can_answer_task(state)
    assert state['steps'][0]['status'] == 'blocked'


def test_display_task_can_complete_after_successful_display():
    state = initialize_task_state('Open this file', required_action='display')

    record_tool_result(state, 'display_file', {'displayed': True})

    assert can_answer_task(state)


def test_resource_discovery_task_requires_a_successful_list_result():
    state = initialize_task_state('List the files attached to this chat')
    assert state['required_action'] == 'discover'

    record_tool_result(state, 'list_chat_files', '{"error":"permission denied"}')
    assert not can_answer_task(state)

    record_tool_result(state, 'list_chat_files', '[{"id":"file-1","filename":"report.xlsx"}]')
    assert can_answer_task(state)


def test_file_existing_is_not_proof_that_the_viewer_opened():
    state = initialize_task_state('Show this file', required_action='display')
    record_tool_result(state, 'display_file', {'exists': True, 'displayed': False})
    assert not can_answer_task(state)


def test_empty_search_requires_complete_scope_coverage():
    state = initialize_task_state('Find the missing row', required_action='search')
    record_tool_result(state, 'grep_chat_files', {'text': 'No matches', 'evidence_ids': []})
    assert not can_answer_task(state)
    record_tool_result(
        state,
        'grep_chat_files',
        {
            'text': 'No matches',
            'evidence_ids': [],
            'coverage_complete': True,
        },
    )
    assert can_answer_task(state)


def test_empty_indexed_search_can_only_complete_when_index_coverage_is_confirmed():
    state = initialize_task_state('Find the missing row', required_action='search')
    record_tool_result(
        state,
        'search_editable_document',
        {
            'status': 'succeeded',
            'results': [],
            'search_complete': False,
        },
    )
    assert not can_answer_task(state)
    record_tool_result(
        state,
        'search_editable_document',
        {
            'status': 'succeeded',
            'results': [],
            'search_complete': True,
            'coverage_complete': False,
            'searched_resource_ids': ['document-1'],
        },
    )
    assert can_answer_task(state)


def test_legacy_error_string_cannot_complete_search():
    state = initialize_task_state('Find a value', required_action='search')
    record_tool_result(state, 'grep_chat_files', 'Error: permission denied')
    assert not can_answer_task(state)


def test_compound_display_and_read_requires_both_effects():
    state = initialize_task_state('Open the file and summarize its contents')
    record_tool_result(state, 'display_file', {'displayed': True})
    assert not can_answer_task(state)


def test_empty_reader_output_with_evidence_id_is_not_content():
    state = initialize_task_state('Read this file')
    record_tool_result(state, 'read_editable_document', {'evidence_id': 'ev-empty', 'text': ''})
    assert not can_answer_task(state)


def test_edit_request_requires_a_reviewable_proposal_not_an_unapproved_commit():
    state = initialize_task_state('Edit this document to fix the title')
    assert state['required_action'] == 'propose'
    assert state['required_actions'] == ['read', 'propose']
    record_tool_result(
        state,
        'propose_document_edit',
        {
            'status': 'ready',
            'preview_id': 'preview-1',
            'approval_required': True,
        },
    )
    assert not can_answer_task(state)
    record_tool_result(
        state,
        'read_editable_document',
        {
            'status': 'succeeded',
            'evidence_id': 'ev-1',
            'text': 'Document title',
        },
    )
    assert can_answer_task(state)


def test_explicit_approval_or_commit_requires_successful_commit():
    state = initialize_task_state('Approve and apply the proposed changes')
    assert state['required_action'] == 'commit'
    record_tool_result(state, 'propose_document_edit', {'status': 'ready', 'preview_id': 'preview-1'})
    assert not can_answer_task(state)
    record_tool_result(state, 'commit_preview', {'status': 'committed', 'revision': 2})
    assert can_answer_task(state)


def test_task_checkpoint_persists_evidence_but_never_raw_document_text():
    state = initialize_task_state('Read this file')
    state['resource_scope_ids'] = ['file-1']
    state['resource_scope_versions'] = {'file-1': 3}
    state['covered_resource_versions'] = {'file-1': 3}
    state['coverage_complete'] = True
    record_tool_result(
        state,
        'read_editable_document',
        {
            'evidence_id': 'ev-1',
            'text': 'private document content',
            'revision': 3,
        },
    )

    checkpoint = task_checkpoint(state)

    assert checkpoint['evidence_ids'] == ['ev-1']
    assert checkpoint['steps'][0]['revision'] == 3
    assert checkpoint['resource_scope_versions'] == {'file-1': 3}
    assert checkpoint['covered_resource_versions'] == {'file-1': 3}
    assert checkpoint['coverage_complete'] is True
    assert 'private document content' not in str(checkpoint)


def test_calculation_requires_input_evidence_and_a_reported_result():
    state = initialize_task_state('Calculate the total from this file', required_action='calculate')
    record_tool_result(
        state,
        'run_document_code',
        {
            'evidence_ids': ['input-ev-1'],
            'stdout': '',
            'exit_code': 0,
        },
    )
    assert not can_answer_task(state)
    record_tool_result(
        state,
        'run_document_code',
        {
            'evidence_ids': ['input-ev-1'],
            'stdout': 'Total: 42',
            'exit_code': 0,
        },
    )
    assert can_answer_task(state)


def test_create_request_requires_a_published_artifact():
    state = initialize_task_state('Create a spreadsheet with the results')
    assert state['required_action'] == 'create'
    record_tool_result(
        state,
        'run_document_code',
        {
            'evidence_ids': ['input-ev-1'],
            'stdout': 'file generated',
        },
    )
    assert not can_answer_task(state)
    record_tool_result(
        state,
        'publish_document_output',
        {
            'document_id': 'doc-new',
            'revision': 1,
            'filename': 'results.xlsx',
        },
    )
    assert can_answer_task(state)


def test_action_inference_distinguishes_text_summary_from_file_creation_and_editing():
    assert initialize_task_state('Create a summary of this file')['required_action'] == 'read'
    assert initialize_task_state('Fill this workbook with the missing values')['required_action'] == 'propose'
    assert initialize_task_state('Export the results to an XLSX file')['required_action'] == 'create'


def test_content_display_and_compound_file_actions_keep_distinct_requirements():
    assert initialize_task_state('Show me what is in this file')['required_action'] == 'read'
    assert initialize_task_state('Open this file')['required_actions'] == ['display']
    assert initialize_task_state('Open the file and summarize its contents')['required_actions'] == [
        'read',
        'display',
    ]

    edit = initialize_task_state('Edit this file to fix the title')
    assert edit['required_actions'] == ['read', 'propose']

    calculation = initialize_task_state('Calculate the total from this file')
    assert calculation['required_actions'] == ['calculate']

    content_filtered_inventory = initialize_task_state('List the attached files that mention tuition')
    assert content_filtered_inventory['required_action'] == 'search'
