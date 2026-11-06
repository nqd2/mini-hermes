"""Versioned document chunks and lexical retrieval for Mini-Hermes resources."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

from sqlalchemy import delete, func, or_, select

from .models import DocumentChunk


def _sections(content: bytes, filename: str) -> list[tuple[str, str, dict]]:
    suffix = Path(filename).suffix.lower()
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
        result = []
        try:
            for sheet in workbook:
                lines = []
                first_row = sheet.min_row or 1
                for row_number, row in enumerate(sheet.iter_rows(), start=first_row):
                    values = [f'{cell.coordinate}={cell.value}' for cell in row if cell.value is not None]
                    if values:
                        lines.append(f'row {row_number}: ' + ' | '.join(values))
                result.append((sheet.title, '\n'.join(lines), {
                    'sheet': sheet.title, 'first_column': get_column_letter(sheet.min_column or 1),
                    'last_column': get_column_letter(sheet.max_column or 1),
                    'first_row': first_row, 'last_row': sheet.max_row or first_row,
                }))
        finally:
            workbook.close()
        return result
    if suffix == '.pdf':
        from pypdf import PdfReader

        return [(f'page {index}', page.extract_text() or '', {'page': index})
                for index, page in enumerate(PdfReader(BytesIO(content)).pages, start=1)]
    if suffix == '.pptx':
        from pptx import Presentation

        deck = Presentation(BytesIO(content))
        return [(f'slide {index}', '\n'.join(shape.text for shape in slide.shapes if shape.has_text_frame),
                 {'slide': index}) for index, slide in enumerate(deck.slides, start=1)]
    if suffix == '.docx':
        from docx import Document as WordDocument

        document = WordDocument(BytesIO(content))
        paragraphs = [p.text for p in document.paragraphs if p.text]
        paragraphs.extend('\t'.join(cell.text for cell in row.cells)
                          for table in document.tables for row in table.rows)
        return [('document', '\n'.join(paragraphs), {})]
    return [('document', content.decode('utf-8', errors='replace'), {})]


def extract_structured_text(content: bytes, filename: str) -> str:
    """Render source structure into bounded readable text without dropping cell coordinates."""
    rendered = []
    for section, text, _metadata in _sections(content, filename):
        rendered.append(f'## {section}\n{text}' if section else text)
    return '\n\n'.join(rendered)


def build_chunk_rows(document_id: str, revision: int, filename: str, content: bytes,
                     *, chunk_chars: int = 1600, overlap_chars: int = 180) -> list[dict]:
    """Build chunks without losing page, sheet, row, or source offset metadata."""
    rows = []
    ordinal = 0
    for section, text, metadata in _sections(content, filename):
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            if end < len(text):
                boundary = text.rfind('\n', start + chunk_chars // 2, end)
                if boundary > start:
                    end = boundary
            body = text[start:end]
            chunk_id = hashlib.sha256(
                f'{document_id}:{revision}:{ordinal}:{section}:{start}:{end}:{body}'.encode()
            ).hexdigest()
            row_metadata = dict(metadata)
            if 'sheet' in row_metadata:
                row_metadata['rows'] = [int(value) for value in re.findall(r'(?m)^row (\d+):', body)]
                if row_metadata['rows']:
                    row_metadata['first_row'] = min(row_metadata['rows'])
                    row_metadata['last_row'] = max(row_metadata['rows'])
            rows.append({
                'id': chunk_id, 'document_id': document_id, 'revision': revision, 'ordinal': ordinal,
                'source_kind': Path(filename).suffix.lower().lstrip('.') or 'text',
                'section': section, 'page': metadata.get('page'), 'start_offset': start,
                'end_offset': end, 'content': body, 'metadata_json': row_metadata,
            })
            ordinal += 1
            if end >= len(text):
                break
            start = max(start + 1, end - overlap_chars)
    return rows


async def replace_document_chunks(db, document_id: str, revision: int, filename: str, content: bytes) -> int:
    await db.execute(delete(DocumentChunk).where(
        DocumentChunk.document_id == document_id, DocumentChunk.revision == revision
    ))
    rows = build_chunk_rows(document_id, revision, filename, content)
    if rows:
        db.add_all(DocumentChunk(**row) for row in rows)
    return len(rows)


async def search_document_chunks(
    db, document_id: str, revision: int, query: str, limit: int = 8
) -> list[DocumentChunk]:
    """Use PostgreSQL FTS + optional trigram score; portable fallback for tests/slim DBs."""
    if not query.strip():
        return []
    base = select(DocumentChunk).where(
        DocumentChunk.document_id == document_id, DocumentChunk.revision == revision
    )
    if db.bind.dialect.name == 'postgresql':
        tsquery = func.plainto_tsquery('simple', query)
        vector = func.to_tsvector('simple', DocumentChunk.content)
        fts = vector.bool_op('@@')(tsquery)
        rank = func.ts_rank(vector, tsquery)
        similarity = func.similarity(DocumentChunk.content, query)
        fuzzy = DocumentChunk.content.op('%')(query)
        statement = base.where(or_(fts, fuzzy)).order_by((rank + similarity).desc()).limit(limit)
        return list((await db.execute(statement)).scalars().all())

    chunks = list((await db.execute(base)).scalars().all())
    terms = [term.casefold() for term in re.findall(r'\w+', query) if len(term) > 1]
    def score(chunk):
        text = chunk.content.casefold()
        exact = sum(text.count(term) for term in terms)
        candidates = re.findall(r'\w+', text)
        fuzzy = max((SequenceMatcher(None, term, candidate).ratio()
                     for term in terms for candidate in candidates), default=0)
        return exact * 2 + (fuzzy if fuzzy >= 0.72 else 0)
    return sorted((chunk for chunk in chunks if score(chunk) > 0), key=score, reverse=True)[:limit]
