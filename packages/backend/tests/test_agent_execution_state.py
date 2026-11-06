from open_webui.mini_hermes.agent_execution import (
    can_answer_task,
    initialize_task_state,
    record_tool_result,
)


def test_display_success_does_not_satisfy_a_content_request():
    state = initialize_task_state('What is in this file?', required_action='read')

    record_tool_result(state, 'display_file', {'displayed': True, 'name': 'M.xlsx'})

    assert not can_answer_task(state)
    assert state['steps'][0]['action'] == 'display'


def test_reader_evidence_satisfies_content_request_and_keeps_provenance():
    state = initialize_task_state('Summarize this document', required_action='read')

    record_tool_result(state, 'read_editable_document', {
        'status': 'ready',
        'revision': 3,
        'evidence_id': 'ev-1',
        'text': 'actual content',
    })

    assert can_answer_task(state)
    assert state['evidence_ids'] == ['ev-1']
    assert state['steps'][0]['revision'] == 3


def test_tool_error_does_not_count_as_completed_search():
    state = initialize_task_state('Find the value', required_action='search')

    record_tool_result(state, 'search_editable_document', {'error': 'permission denied'})

    assert not can_answer_task(state)
    assert state['steps'][0]['status'] == 'blocked'


def test_display_task_can_complete_after_successful_display():
    state = initialize_task_state('Open this file', required_action='display')

    record_tool_result(state, 'display_file', {'displayed': True})

    assert can_answer_task(state)
