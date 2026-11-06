from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.chunks import build_chunk_rows, replace_document_chunks, search_document_chunks
from open_webui.mini_hermes.models import DocumentChunk


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
    assert chunks[0]['revision'] == 3
    assert len(chunks[0]['id']) == 64


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
