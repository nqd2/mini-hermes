import hashlib
from types import SimpleNamespace

import pytest

from open_webui.mini_hermes.provenance import content_version, evidence_id
from open_webui.utils.chat_runtime.citations import get_citation_source_from_tool_result, get_source_context
from open_webui.utils.json_codec import JSONCodec


def test_source_context_exposes_bounded_resource_provenance_to_model():
    context = get_source_context(
        [
            {
                'source': {'id': 'upload-1', 'name': 'M.xlsx', 'type': 'file'},
                'document': ['Program 301 - Computer Science'],
                'metadata': [
                    {
                        'file_id': 'upload-1',
                        'source': 'M.xlsx',
                        'evidence_id': 'evidence-301',
                        'revision': 4,
                        'start_offset': 1200,
                        'end_offset': 1233,
                        'sheet': 'Programs',
                        'last_row': 301,
                        'private_internal_value': 'do not include',
                    }
                ],
            }
        ]
    )

    assert 'evidence_id="evidence-301"' in context
    assert 'revision="4"' in context
    assert 'start_offset="1200"' in context
    assert 'end_offset="1233"' in context
    assert 'sheet="Programs"' in context
    assert 'last_row="301"' in context
    assert 'private_internal_value' not in context


def test_view_file_citation_keeps_versioned_span():
    source = get_citation_source_from_tool_result(
        'view_file',
        {},
        JSONCodec.dumps(
            {
                'id': 'file-1',
                'filename': 'M.xlsx',
                'content': 'row data',
                'revision': 'version-hash',
                'evidence_id': 'evidence-1',
                'start_offset': 20,
                'end_offset': 28,
            }
        ),
    )

    assert source[0]['document'] == ['row data']
    assert source[0]['metadata'][0]['revision'] == 'version-hash'
    assert source[0]['metadata'][0]['evidence_id'] == 'evidence-1'
    assert source[0]['metadata'][0]['start_offset'] == 20


def test_view_file_without_content_evidence_cannot_create_a_citation():
    source = get_citation_source_from_tool_result(
        'view_file',
        {},
        JSONCodec.dumps({'id': 'file-1', 'filename': 'M.xlsx', 'content': 'row data'}),
    )

    assert source == []


def test_exact_search_citation_keeps_line_and_version_provenance():
    source = get_citation_source_from_tool_result(
        'grep_chat_files',
        {},
        JSONCodec.dumps(
            {
                'coverage_complete': True,
                'results': [
                    {
                        'file_id': 'file-1',
                        'filename': 'M.xlsx',
                        'line_number': 7,
                        'revision': 'version-hash',
                        'evidence_id': 'evidence-2',
                        'start_offset': 44,
                        'end_offset': 60,
                        'text': 'compsci related row',
                    }
                ],
            }
        ),
    )

    assert source[0]['document'] == ['compsci related row']
    assert source[0]['metadata'][0]['evidence_id'] == 'evidence-2'
    assert source[0]['metadata'][0]['line'] == 7
    assert source[0]['metadata'][0]['revision'] == 'version-hash'


@pytest.mark.asyncio
async def test_knowledge_file_reader_emits_provenance_used_by_citations(monkeypatch):
    from open_webui.models.files import Files
    from open_webui.models.groups import Groups
    from open_webui.models.knowledge import Knowledges
    from open_webui.tools import builtin

    content = 'first page\nsecond section'
    file = SimpleNamespace(
        id='knowledge-file-1',
        filename='knowledge.txt',
        user_id='alice',
        data={'content': content},
        updated_at=None,
        created_at=None,
    )

    async def get_groups(*args, **kwargs):
        return []

    async def get_file(*args, **kwargs):
        return file

    async def get_knowledges(*args, **kwargs):
        return []

    monkeypatch.setattr(Groups, 'get_groups_by_member_id', get_groups)
    monkeypatch.setattr(Files, 'get_file_by_id', get_file)
    monkeypatch.setattr(Files, 'get_file_by_id_without_content', get_file)
    monkeypatch.setattr(Knowledges, 'get_knowledges_by_file_id', get_knowledges)

    result = JSONCodec.loads(
        await builtin.view_knowledge_file(
            'knowledge-file-1',
            offset=6,
            max_chars=7,
            __request__=object(),
            __user__={'id': 'alice', 'role': 'user'},
        )
    )

    assert result['revision'] == hashlib.sha256(content.encode()).hexdigest()
    assert result['start_offset'] == 6
    assert result['end_offset'] == 13
    citation = get_citation_source_from_tool_result('view_knowledge_file', {}, JSONCodec.dumps(result))
    assert citation[0]['metadata'][0]['evidence_id'] == result['evidence_id']
    assert citation[0]['metadata'][0]['start_offset'] == 6


@pytest.mark.asyncio
async def test_line_number_rendering_does_not_change_the_underlying_evidence_span(monkeypatch):
    from open_webui.models.files import Files
    from open_webui.tools import builtin

    content = 'first line\nsecond line\nthird line'
    file = SimpleNamespace(
        id='file-1',
        filename='notes.txt',
        user_id='alice',
        data={'content': content},
        updated_at=None,
        created_at=None,
    )

    async def get_file(*_args, **_kwargs):
        return file

    monkeypatch.setattr(Files, 'get_file_by_id', get_file)
    monkeypatch.setattr(Files, 'get_file_by_id_without_content', get_file)
    result = JSONCodec.loads(
        await builtin.view_file(
            'file-1',
            offset=11,
            max_chars=11,
            line_numbers=True,
            __request__=object(),
            __user__={'id': 'alice', 'role': 'user'},
        )
    )

    expected_text = 'second line'
    expected_version = content_version(content)
    assert result['content'] == '2: second line'
    assert result['revision'] == expected_version
    assert result['evidence_id'] == evidence_id('file-1', expected_version, 11, 22, expected_text)
