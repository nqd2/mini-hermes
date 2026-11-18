"""V1 Excel pagination characterization; no V2 service or LLM is involved."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.documents.chunks import build_chunk_rows, extract_structured_text
from weai.agent_runtime.agent_execution import initialize_task_state
from weai.agent_runtime.execution.pipeline import ResourceRequestPipeline
from weai.documents.models import Document, DocumentChunk, Folder, Revision
from weai.utils.chat_runtime.resource_execution import prepare_resource_tool_result
from weai.utils.json_codec import JSONCodec

FIXTURES = Path(__file__).parent / 'fixtures' / 'v2'


@pytest_asyncio.fixture(params=['xlsx-f05-synthetic.xlsx', 'xlsx-f05-exact-57765.xlsx'], ids=['large', 'exact-57765'])
async def indexed_workbook(monkeypatch, request):
    from weai.documents import resource_service
    from weai.agent_runtime import tools

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(sync, tables=[
            Folder.__table__, Document.__table__, Revision.__table__, DocumentChunk.__table__,
        ]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = FIXTURES / request.param
    content = fixture.read_bytes()
    document = Document(id='doc-fixture', owner_id='owner', filename=fixture.name,
                        mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        current_revision=1, source_file_id='upload-fixture')
    async with sessions() as db:
        db.add(document)
        db.add(Revision(id='revision-fixture', document_id=document.id, number=1,
                        object_key='unused', sha256='a' * 64, size=len(content)))
        db.add_all(DocumentChunk(**row) for row in build_chunk_rows(document.id, 1, fixture.name, content))
        await db.commit()

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(tools, 'get_async_db', open_db)
    monkeypatch.setattr(tools, '_scoped_document', AsyncMock(return_value=document))
    monkeypatch.setattr(tools, 'ObjectStore', lambda: object())
    monkeypatch.setattr(resource_service, 'ensure_revision_index', AsyncMock(return_value=(1, None, None)))
    try:
        yield tools, document, content
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_excel_v1_6000_character_page_exceeds_5952_serialized_budget(indexed_workbook):
    tools, document, _ = indexed_workbook
    result = await tools.read_editable_document(document.id, limit=6000, __user__={'id': 'owner'})
    content, supplied = prepare_resource_tool_result(
        {'resource_aware_files': True, 'resource_file_context_char_budget': 5952},
        'read_editable_document', result,
    )
    assert len(result['text']) == 6000
    assert supplied is False
    error = JSONCodec.loads(content)
    assert error['code'] == 'tool_result_too_large'
    assert error['original_chars'] > 5952
    assert error['limit_chars'] == 5952


@pytest.mark.asyncio
async def test_excel_v1_4000_character_pages_reconstruct_rendered_fixture(indexed_workbook):
    tools, document, content = indexed_workbook
    expected = extract_structured_text(content, document.filename)
    state = initialize_task_state('Summarize the entire document')
    state.update(requires_complete_coverage=True, resource_scope_ids=[document.id],
                 resource_scope_versions={document.id: 1})
    metadata = {'resource_aware_files': True, 'resource_file_context_char_budget': 5952,
                'resource_evidence_requested': True, 'agent_task_state': state}
    offset = 0
    pages = []
    while True:
        result = await tools.read_editable_document(document.id, offset=offset, limit=4000, __user__={'id': 'owner'})
        serialized, supplied = prepare_resource_tool_result(
            metadata, 'read_editable_document', result,
        )
        assert supplied is True and len(serialized) <= 5952
        assert result['offset'] == offset
        assert result['total_characters'] == len(expected)
        pages.append(result['text'])
        next_offset = result['next_offset']
        if next_offset is None:
            break
        assert next_offset == offset + len(result['text']) and next_offset > offset
        offset = next_offset
        assert len(pages) < 100
    assert len(pages) > 8
    if document.filename == 'xlsx-f05-exact-57765.xlsx':
        assert len(expected) == 57_765 and len(pages) == 15
    assert ''.join(pages) == expected
    # V1 stores evidence IDs, but does not join read ranges into full coverage.
    assert state['coverage_complete'] is False
    assert state['covered_resource_versions'] == {}
    assert len(state['evidence_ids']) == len(pages)
    assert ResourceRequestPipeline.decision(metadata).status == 'partial'

@pytest.mark.asyncio
async def test_excel_v1_citation_matches_the_page_supplied_to_model(indexed_workbook):
    from weai.utils.chat_runtime.citations import get_citation_source_from_tool_result

    tools, document, _ = indexed_workbook
    result = await tools.read_editable_document(document.id, offset=4100, limit=4000, __user__={'id': 'owner'})
    serialized, supplied = prepare_resource_tool_result(
        {'resource_aware_files': True, 'resource_file_context_char_budget': 5952},
        'read_editable_document', result,
    )
    assert supplied is True
    model_page = JSONCodec.loads(serialized)
    sources = get_citation_source_from_tool_result('read_editable_document', {}, result)
    assert len(sources) == 1
    assert sources[0]['document'] == [model_page['text']]
    metadata = sources[0]['metadata'][0]
    assert metadata['evidence_id'] == model_page['evidence_id']
    assert metadata['revision'] == model_page['revision']
    assert metadata['start_offset'] == model_page['offset']
    assert metadata['end_offset'] == model_page['next_offset']
