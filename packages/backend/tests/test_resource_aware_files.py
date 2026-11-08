from open_webui.mini_hermes.resource_aware import (
    bounded_file_sources,
    bounded_tool_output,
    has_resource_file_tools,
    is_independent_greeting,
    plan_resource_context,
    requires_resource_evidence,
    resource_aware_context_enabled,
    resource_aware_file_ids,
    resource_file_context_budget,
    resolve_resource_scope,
)


def test_only_uploaded_document_references_activate_resource_aware_mode():
    files = [
        {'type': 'file', 'id': 'one', 'name': 'M.xlsx'},
        {'type': 'image', 'id': 'two'},
        {'type': 'collection', 'id': 'three'},
    ]

    assert resource_aware_file_ids(files) == ['one']


def test_resource_file_tool_routing_uses_tools_registered_for_this_request():
    assert not has_resource_file_tools(None)
    assert not has_resource_file_tools({'search_web': {}})
    assert not has_resource_file_tools({'list_chat_files': {}})
    assert not has_resource_file_tools({'display_file': {}})
    assert has_resource_file_tools({'read_editable_document': {}})
    assert has_resource_file_tools({'view_file': {}})


def test_resource_evidence_is_loaded_for_references_but_not_unrelated_messages():
    assert requires_resource_evidence('File này về cái gì?')
    assert requires_resource_evidence('Còn file thứ hai thì sao?')
    assert requires_resource_evidence('Summarize the attached document')
    assert requires_resource_evidence(
        'Trong những ngành thạc sĩ này có ngành nào liên quan đến compsci không? Chỉ ra cho tôi với.'
    )
    assert not requires_resource_evidence('hi')
    assert not requires_resource_evidence('What is the weather today?')
    assert requires_resource_evidence('tóm tắt')
    assert requires_resource_evidence('summarize')


def test_window_evidence_preserves_source_chunk_and_version():
    def select(revision):
        return bounded_file_sources([{
            'source': {'id': 'file-1'}, 'document': ['same content'],
            'metadata': [{'revision': revision, 'evidence_id': f'chunk-{revision}',
                          'start_offset': 100}],
        }], '')[0]['metadata'][0]
    first, second = select(1), select(2)
    assert first['evidence_id'] == 'chunk-1'
    assert second['evidence_id'] == 'chunk-2'
    assert first['start_char'] == 100


def test_existing_chat_resources_keep_resource_mode_without_current_message_attachment(monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'true')
    assert resource_aware_context_enabled([], has_chat_resources=True)
    assert not resource_aware_context_enabled([], has_chat_resources=False)
    monkeypatch.setenv('MH_RESOURCE_AWARE_FILES', 'false')
    assert not resource_aware_context_enabled([{'id': 'file-1', 'type': 'file'}], has_chat_resources=True)


def test_file_context_budget_reserves_space_and_caps_large_windows():
    assert resource_file_context_budget(16_384, 4_096) == 6_144
    assert resource_file_context_budget(128_000, 4_096) == 24_000
    assert resource_file_context_budget(2_000, 2_000) == 0


def test_duplicate_or_unresolved_file_references_are_ignored():
    files = [{'type': 'file', 'id': 'one'}, {'type': 'file', 'id': 'one'}, {'type': 'file', 'name': 'pending'}]

    assert resource_aware_file_ids(files) == ['one']


def test_greeting_skips_files_but_followup_uses_conversation_state():
    assert is_independent_greeting('hi', pending_task=False)
    assert not is_independent_greeting('hi', pending_task=True)
    assert not is_independent_greeting('Còn file thứ hai thì sao?', pending_task=False)


def test_resource_scope_resolves_ordinals_and_flags_ambiguous_deictic_references():
    files = [
        {'type': 'file', 'id': 'f1', 'name': 'first.xlsx'},
        {'type': 'file', 'id': 'f2', 'name': 'second.pdf'},
    ]
    assert resolve_resource_scope('Còn file thứ hai thì sao?', files) == (['f2'], False)
    assert resolve_resource_scope('What is in this file?', files) == ([], True)
    assert resolve_resource_scope('List all attached files', files) == (['f1', 'f2'], False)


def test_toolless_sources_are_bounded_and_keep_offsets():
    sources = [
        {
            'source': {'type': 'file', 'id': 'one', 'name': 'M.xlsx'},
            'document': ['unrelated ' * 20000 + 'computer science degree' + ' unrelated' * 20000],
            'metadata': [{'file_id': 'one', 'source': 'M.xlsx'}],
        }
    ]

    result = bounded_file_sources(sources, 'computer science', max_total_chars=5000, window_chars=2500)

    assert sum(len(doc) for source in result for doc in source['document']) <= 5000
    assert 'computer science' in result[0]['document'][0]
    assert result[0]['metadata'][0]['start_char'] > 0
    assert result[0]['metadata'][0]['truncated'] is True


def test_oversized_tool_result_returns_explicit_error():
    result = bounded_tool_output('x' * 12001, max_chars=12000)
    assert result['code'] == 'tool_result_too_large'
    assert result['original_chars'] == 12001
    assert 'content' not in result


def test_multiple_files_get_a_bounded_window_each():
    sources = [
        {'source': {'type': 'file', 'id': str(index)}, 'document': ['x' * 20000], 'metadata': [{'file_id': str(index)}]}
        for index in range(2)
    ]
    result = bounded_file_sources(sources, 'summarize', max_total_chars=6000, window_chars=4000)
    assert [source['source']['id'] for source in result] == ['0', '1']
    assert all(len(source['document'][0]) == 3000 for source in result)


def test_current_attachment_is_prioritized_over_older_chat_file():
    sources = [
        {'source': {'type': 'file', 'id': str(index)}, 'document': ['x' * 20000], 'metadata': [{'file_id': str(index)}]}
        for index in range(2)
    ]
    result = bounded_file_sources(sources, 'summarize this file', priority_file_ids=['1'], max_total_chars=3000)
    assert [source['source']['id'] for source in result] == ['1', '0']


def test_context_planner_uses_none_focused_and_full_request_modes():
    assert plan_resource_context('hi') == 'NONE'
    assert plan_resource_context('What is on the second sheet?') == 'FOCUSED'
    assert plan_resource_context('Tóm tắt toàn bộ tài liệu này') == 'FULL'
    assert plan_resource_context('hi', has_pending_task=True) == 'FOCUSED'


def test_explicit_full_context_includes_whole_resource_only_if_budget_fits():
    source = {
        'source': {'id': 'file-1'}, 'document': ['all content'],
        'metadata': [{'file_id': 'file-1', 'evidence_id': 'chunk-1', 'coverage_complete': True}],
    }
    full = bounded_file_sources([source], 'summarize all', max_total_chars=100, full_if_fits=True)
    assert full[0]['document'] == ['all content']
    assert full[0]['metadata'][0]['truncated'] is False
    assert full[0]['metadata'][0]['evidence_id'] == 'chunk-1'

    too_large = bounded_file_sources([source], 'summarize all', max_total_chars=4, full_if_fits=True)
    assert too_large[0]['metadata'][0]['truncated'] is True

    partial = {'source': {'id': 'file-1'}, 'document': ['excerpt'], 'metadata': [{'file_id': 'file-1'}]}
    not_full = bounded_file_sources([partial], 'summarize all', max_total_chars=100, full_if_fits=True)
    assert not_full[0]['metadata'][0].get('coverage_complete') is not True
