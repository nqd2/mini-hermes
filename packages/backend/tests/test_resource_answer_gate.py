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
