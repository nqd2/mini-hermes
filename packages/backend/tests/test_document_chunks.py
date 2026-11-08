from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.chunks import (
    build_chunk_rows, document_range_evidence, extract_structured_text, read_document_range, replace_document_chunks,
    search_document_chunks,
)
from open_webui.mini_hermes.models import DocumentChunk
from open_webui.mini_hermes.library import SUPPORTED_RESOURCE_SUFFIXES


def test_spreadsheet_chunks_keep_sheet_row_and_cell_coordinates():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Masters'
    sheet['A1'] = 'Program'
    sheet['B1'] = 'Degree'
    sheet['A2'] = 'Computer Science'
    sheet['B2'] = 'Master'
    sheet['D4'] = '=COUNTA(A2:A2)'
    buffer = BytesIO()
    workbook.save(buffer)

    chunks = build_chunk_rows('doc-1', 3, 'programs.xlsx', buffer.getvalue(), chunk_chars=1000)

    assert len(chunks) == 1
    assert chunks[0]['section'] == 'Masters'
    assert chunks[0]['metadata_json']['rows'] == [1, 2, 4]
    assert 'A2=Computer Science' in chunks[0]['content']
    assert 'D4==COUNTA(A2:A2)' in chunks[0]['content']
    assert 'D4==COUNTA(A2:A2) [cached=None]' in chunks[0]['content']
    assert chunks[0]['revision'] == 3
    assert len(chunks[0]['id']) == 64


def test_csv_chunks_keep_cell_coordinates_and_json_structure():
    assert {'.txt', '.md', '.csv', '.tsv', '.json'} <= SUPPORTED_RESOURCE_SUFFIXES
    csv_chunks = build_chunk_rows(
        'csv-1', 1, 'programs.csv', b'Program,Degree\nComputer Science,Master\n'
    )
    assert csv_chunks[0]['metadata_json']['sheet'] == 'Table'
    assert 'A2=Computer Science' in csv_chunks[0]['content']
    assert 'B2=Master' in csv_chunks[0]['content']

    multiline_csv = build_chunk_rows(
        'csv-2', 1, 'notes.csv', b'Name,Note\nAlpha,"line one\nline two"\n'
    )
    assert 'B2=line one\nline two' in multiline_csv[0]['content']

    json_chunks = build_chunk_rows('json-1', 1, 'programs.json', b'{"degree":"Master"}')
    assert '"degree": "Master"' in json_chunks[0]['content']


def test_range_evidence_uses_global_offsets_and_exposes_next_page():
    page = document_range_evidence('doc-1', 'long.txt', 4, 100, 'page text', 500)

    assert page['offset'] == 100
    assert page['text'] == 'page text'
    assert page['next_offset'] == 109
    assert page['total_characters'] == 500

    last_page = document_range_evidence('doc-1', 'long.txt', 4, 491, 'last page', 500)
    assert last_page['next_offset'] is None


@pytest.mark.asyncio
async def test_lexical_search_returns_versioned_chunk_evidence():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(DocumentChunk.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([
            DocumentChunk(id='a' * 64, document_id='doc-1', revision=2, ordinal=0,
                          source_kind='xlsx', section='Arts', start_offset=0, end_offset=30,
                          content='Program: History and Culture', metadata_json={'sheet': 'Arts'}),
            DocumentChunk(id='b' * 64, document_id='doc-1', revision=2, ordinal=1,
                          source_kind='xlsx', section='Tech', start_offset=0, end_offset=31,
                          content='Program: Computer Science Master', metadata_json={'sheet': 'Tech'}),
        ])
        await db.commit()
        results = await search_document_chunks(db, 'doc-1', 2, 'computer science', 5)
        assert [item.id for item in results] == ['b' * 64]
        assert results[0].metadata_json == {'sheet': 'Tech'}
    await engine.dispose()


@pytest.mark.asyncio
async def test_chunk_reindex_is_idempotent_for_same_revision():
    workbook = Workbook()
    workbook.active['A1'] = 'unique-signal'
    buffer = BytesIO()
    workbook.save(buffer)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(DocumentChunk.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        first_count = await replace_document_chunks(db, 'doc-1', 1, 'file.xlsx', buffer.getvalue())
        await db.commit()
        second_count = await replace_document_chunks(db, 'doc-1', 1, 'file.xlsx', buffer.getvalue())
        await db.commit()
        results = await search_document_chunks(db, 'doc-1', 1, 'unique-signal', 10)
        assert first_count == second_count == len(results) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_bounded_range_reads_reconstruct_chunk_overlaps_without_reparsing():
    content = ('alpha beta gamma delta epsilon zeta\n' * 20).encode()
    rows = build_chunk_rows('doc-1', 1, 'source.txt', content, chunk_chars=80, overlap_chars=12)
    expected = extract_structured_text(content, 'source.txt')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(DocumentChunk.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all(DocumentChunk(**row) for row in rows)
        await db.commit()
        full, total = await read_document_range(db, 'doc-1', 1, 0, len(expected) + 1)
        assert full == expected
        assert total == len(expected)
        offset = 75
        bounded, reported_total = await read_document_range(db, 'doc-1', 1, offset, 91)
        assert bounded == expected[offset:offset + 91]
        assert reported_total == len(expected)
    await engine.dispose()
