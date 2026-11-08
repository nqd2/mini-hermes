from types import SimpleNamespace

from open_webui.mini_hermes.provenance import content_version
from open_webui.tools import document_resources
from open_webui.tools.builtin import (
    _filter_resource_scope,
    _grep_chat_file_models,
    grep_chat_files,
    list_chat_files,
    query_chat_files,
)
from open_webui.tools.document_resources import _format_chat_file_sources
from open_webui.utils.json_codec import JSONCodec


def test_chat_file_tools_keep_legacy_builtin_imports():
    assert _filter_resource_scope is document_resources._filter_resource_scope
    assert _grep_chat_file_models is document_resources._grep_chat_file_models
    assert grep_chat_files is document_resources.grep_chat_files
    assert list_chat_files is document_resources.list_chat_files
    assert query_chat_files is document_resources.query_chat_files


def test_semantic_chat_file_results_keep_version_and_evidence_provenance():
    chunks = _format_chat_file_sources(
        [{
            'source': {'id': 'file-1', 'name': 'report.txt'},
            'document': ['A relevant passage'],
            'metadata': [{
                'file_id': 'file-1',
                'revision': 'revision-4',
                'evidence_id': 'evidence-123',
                'start_offset': 40,
                'end_offset': 58,
            }],
            'distances': [0.12],
        }],
        {'file-1': 'current-revision'},
    )

    assert chunks[0]['revision'] == 'revision-4'
    assert chunks[0]['evidence_id'] == 'evidence-123'
    assert chunks[0]['start_offset'] == 40
    assert chunks[0]['end_offset'] == 58
    assert chunks[0]['distance'] == 0.12
    assert chunks[0]['content'] == 'A relevant passage'


def test_scoped_exact_search_returns_versioned_match_evidence():
    first = SimpleNamespace(
        id='file-1',
        filename='one.txt',
        data={'content': 'header\ncomputer science\nfooter'},
    )
    second = SimpleNamespace(
        id='file-2',
        filename='two.txt',
        data={'content': 'unrelated content'},
    )

    result = JSONCodec.loads(_grep_chat_file_models([first], 'computer science'))

    assert result['coverage_complete'] is True
    assert result['searched_file_ids'] == ['file-1']
    assert result['total_matches'] == 1
    match = result['results'][0]
    assert match['line_number'] == 2
    assert match['revision'] == content_version(first.data['content'])
    assert match['evidence_id'] in result['evidence_ids']

    scoped = _filter_resource_scope([({'id': 'file-1'}, first), ({'id': 'file-2'}, second)], ['file-2'])
    assert [file.id for _item, file in scoped] == ['file-2']


def test_empty_exact_search_reports_complete_coverage_for_empty_result():
    file = SimpleNamespace(id='file-1', filename='one.txt', data={'content': 'no target here'})

    result = JSONCodec.loads(_grep_chat_file_models([file], 'target absent'))

    assert result['results'] == []
    assert result['evidence_ids'] == []
    assert result['coverage_complete'] is True
    assert result['searched_file_ids'] == ['file-1']
