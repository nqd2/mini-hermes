import pytest


@pytest.mark.asyncio
async def test_summary_accepts_document_list_after_tool_result_processing():
    from open_webui.utils.chat_runtime.tool_results import process_tool_result
    from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result
    from open_webui.utils.chat_runtime.resource_context_preparation import apply_resource_summary_tool_choice

    state = metadata()
    state.update(resource_summary_task_required=True, resource_summary_task_phase='list')
    result, _, _ = await process_tool_result(
        None, 'list_editable_documents', [{'id': 'doc-1', 'filename': 'book.xlsx'}], 'builtin'
    )
    prepare_resource_tool_result(state, 'list_editable_documents', result)
    form = {'tools': [{'function': {'name': 'start_document_summary_task'}}]}
    apply_resource_summary_tool_choice(form, state)
    assert form['tool_choice']['function']['name'] == 'start_document_summary_task'


@pytest.mark.parametrize('documents', [[], [{'id': 'doc-1'}, {'id': 'doc-2'}], [{}]])
@pytest.mark.asyncio
async def test_summary_rejects_ambiguous_or_missing_processed_document_list(documents):
    from open_webui.utils.chat_runtime.tool_results import process_tool_result
    from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result

    state = metadata()
    state.update(resource_summary_task_required=True, resource_summary_task_phase='list')
    result, _, _ = await process_tool_result(None, 'list_editable_documents', documents, 'builtin')
    prepare_resource_tool_result(state, 'list_editable_documents', result)
    assert state['resource_summary_task_phase'] == 'failed'
    assert state['agent_task_state']['reason'] == 'resource_summary_document_unavailable'


def metadata():
    return {
        'resource_aware_files': True,
        'resource_execution_requested': True,
        'resource_evidence_requested': True,
        'agent_task_state': {'required_actions': ['read'], 'steps': [], 'evidence_ids': []},
    }


def test_gate_holds_prose_but_keeps_tools_visible(monkeypatch):
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    gate = ResourceAnswerGate(metadata())
    output = [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'unverified'}]},
        {'type': 'function_call', 'name': 'list_chat_files'},
    ]
    assert gate.present(output) == output[1:]
    assert output[0]['content'][0]['text'] == 'unverified'
    final = gate.present(output, final=True)
    assert final[-1]['type'] == 'message'
    assert final[-1]['content'][0]['text'] != 'unverified'


def test_gate_releases_answer_only_after_real_evidence(monkeypatch):
    from open_webui.mini_hermes.agent_execution import record_tool_result
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    state = metadata()
    gate = ResourceAnswerGate(state)
    record_tool_result(state['agent_task_state'], 'read_editable_document', {'text': 'value', 'evidence_id': 'ev'})
    output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'verified'}]}]
    assert gate.present(output) == []
    assert gate.present(output, final=True) == output


def test_rejected_tool_output_is_not_recorded_as_supplied_evidence():
    from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result

    state = metadata()
    state['resource_file_context_char_budget'] = 100
    content, supplied = prepare_resource_tool_result(
        state,
        'read_editable_document',
        {'text': 'x' * 200, 'evidence_id': 'ev'},
    )
    assert not supplied
    assert state['agent_task_state']['evidence_ids'] == []
    assert state['agent_task_state']['status'] == 'blocked'
    assert 'tool_result_too_large' in content


def test_gate_flag_off_preserves_wire_output(monkeypatch):
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'false')
    output = [{'type': 'message', 'content': []}]
    assert ResourceAnswerGate(metadata()).present(output) == output


def test_stream_gate_drops_provisional_text_and_preserves_tool_progress(monkeypatch):
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    gate = ResourceAnswerGate(metadata())
    assert (
        gate.present_event(
            {
                'type': 'response:completion',
                'data': {'type': 'response.output_text.delta', 'delta': 'unverified'},
            }
        )
        is None
    )
    tool_event = {
        'type': 'response:completion',
        'data': {'output': [{'type': 'function_call', 'name': 'read_editable_document'}]},
    }
    assert gate.present_event(tool_event) == tool_event


def test_provider_stream_payload_suppresses_unverified_content(monkeypatch):
    from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    gate = ResourceAnswerGate(metadata())
    payload = {'choices': [{'delta': {'content': 'unverified', 'reasoning_content': 'guess'}}]}
    filtered = gate.filter_provider_payload(payload)
    assert filtered['choices'][0]['delta'] == {'content': '', 'reasoning_content': ''}
    assert payload['choices'][0]['delta']['content'] == 'unverified'


def test_large_summary_task_is_durable_and_reports_actionable_pending_state(monkeypatch):
    from open_webui.mini_hermes.agent_execution import task_checkpoint
    from open_webui.utils.chat_runtime.resource_context_preparation import apply_resource_summary_tool_choice
    from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result, ResourceAnswerGate

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    state = metadata()
    state.update(
        resource_scope_ids=['upload-1'],
        resource_scope_versions={'upload-1': 1},
        resource_summary_task_required=True,
        resource_summary_task_phase='list',
        resource_file_context_char_budget=6000,
        agent_task_state={
            'goal': 'tóm tắt file này đi',
            'required_action': 'read',
            'required_actions': ['read'],
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'resource_scope_ids': ['upload-1'],
            'resource_scope_versions': {'upload-1': 1},
            'covered_resource_versions': {},
            'status': 'in_progress',
            'steps': [],
        },
    )
    tool_fields = {
        'tools': [
            {'type': 'function', 'function': {'name': name}}
            for name in ('list_editable_documents', 'start_document_summary_task', 'get_document_task')
        ],
    }

    _, supplied = prepare_resource_tool_result(state, 'list_editable_documents', [{'id': 'doc-1'}])
    assert supplied
    assert state['resource_summary_task_phase'] == 'start'
    apply_resource_summary_tool_choice(tool_fields, state)
    assert tool_fields['tool_choice'] == {
        'type': 'function',
        'function': {'name': 'start_document_summary_task'},
    }

    _, supplied = prepare_resource_tool_result(
        state,
        'start_document_summary_task',
        {'task_id': 'task-1', 'status': 'pending'},
    )
    assert supplied
    assert state['agent_task_state']['resource_task_ids'] == ['task-1']
    assert state['agent_task_state']['reason'] == 'resource_summary_task_pending'
    assert task_checkpoint(state['agent_task_state'])['resource_task_ids'] == ['task-1']
    apply_resource_summary_tool_choice(tool_fields, state)
    assert tool_fields['tool_choice'] == 'none'

    final = ResourceAnswerGate(state).present(
        [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'partial answer'}]}],
        final=True,
    )
    assert 'tệp đính kèm' in final[-1]['content'][0]['text']
    assert 'task-1' in final[-1]['content'][0]['text']
    assert 'Continue Response' in final[-1]['content'][0]['text']


def test_completed_summary_task_satisfies_full_coverage_gate(monkeypatch):
    from open_webui.utils.chat_runtime.resource_context_preparation import apply_resource_summary_tool_choice
    from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result
    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    monkeypatch.setenv('MH_AGENT_EXECUTION_V1', 'true')
    state = metadata()
    state.update(
        resource_scope_ids=['upload-1'],
        resource_scope_versions={'upload-1': 1},
        resource_summary_task_required=True,
        resource_summary_task_phase='status',
        agent_task_state={
            'goal': 'Summarize this file',
            'required_action': 'read',
            'required_actions': ['read'],
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'resource_scope_ids': ['upload-1'],
            'resource_scope_versions': {'upload-1': 1},
            'covered_resource_versions': {},
            'status': 'pending',
            'steps': [],
            'resource_task_ids': ['task-1'],
        },
    )
    content, supplied = prepare_resource_tool_result(
        state,
        'get_document_task',
        {
            'task_id': 'task-1',
            'status': 'completed',
            'coverage_complete': True,
            'result': 'The workbook lists graduate programs and admissions requirements.',
            'evidence_ids': ['chunk-1'],
            'resource_id': 'upload-1',
            'revision': 1,
        },
    )
    assert supplied
    assert 'graduate programs' in content
    assert state['agent_task_state']['coverage_complete'] is True
    assert ResourceRequestPipeline.decision(state).status == 'completed'
    apply_resource_summary_tool_choice(
        {'tools': [{'type': 'function', 'function': {'name': 'get_document_task'}}]},
        state,
    )
    assert state['resource_summary_task_phase'] == 'completed'


def test_index_pending_summary_can_resume_after_document_indexing(monkeypatch):
    from open_webui.mini_hermes.agent_execution import task_checkpoint
    from open_webui.utils.chat_runtime.resource_context_preparation import apply_resource_summary_tool_choice
    from open_webui.utils.chat_runtime.resource_execution import advance_resource_summary_task_phase, ResourceAnswerGate

    state = metadata()
    state.update(
        resource_summary_task_required=True,
        resource_summary_task_phase='start',
        agent_task_state={
            'status': 'in_progress',
            'steps': [],
            'resource_task_ids': [],
        },
    )

    advance_resource_summary_task_phase(
        state,
        'start_document_summary_task',
        {'status': 'pending', 'document_id': 'doc-1', 'index_job_ids': ['index-1']},
    )

    checkpoint = task_checkpoint(state['agent_task_state'])
    assert state['resource_summary_task_phase'] == 'awaiting_index'
    assert state['agent_task_state']['status'] == 'pending'
    assert state['agent_task_state']['reason'] == 'resource_index_pending'
    assert checkpoint['resource_index_job_ids'] == ['index-1']
    assert 'index-1' in ResourceAnswerGate(state).fallback_text_for_current_state()
    tool_fields = {'tools': [{'type': 'function', 'function': {'name': name}} for name in (
        'list_editable_documents', 'start_document_summary_task', 'get_document_task'
    )]}
    apply_resource_summary_tool_choice(tool_fields, state)
    assert tool_fields['tool_choice'] == 'none'
