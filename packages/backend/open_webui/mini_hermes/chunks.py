"""Versioned document chunks and lexical retrieval for Mini-Hermes resources."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import delete, func, or_, select

from .models import DocumentChunk
from .parsers import extract_sections

_SEARCH_STOPWORDS = {
    'a', 'about', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'can', 'could', 'do', 'does',
    'for', 'from', 'how', 'i', 'in', 'is', 'it', 'me', 'of', 'on', 'or', 'please', 'tell',
    'that', 'the', 'these', 'this', 'to', 'was', 'were', 'what', 'when', 'where', 'which',
    'who', 'why', 'with', 'would', 'có', 'của', 'đến', 'là', 'nào', 'và', 'về', 'trong',
}


def extract_structured_text(content: bytes, filename: str) -> str:
    """Render source structure into bounded readable text without dropping cell coordinates."""
    rendered = []
    for section, text, _metadata in extract_sections(content, filename):
        rendered.append(f'## {section}\n{text}' if section else text)
    return '\n\n'.join(rendered)


def build_chunk_rows(document_id: str, revision: int, filename: str, content: bytes,
                     *, chunk_chars: int = 1600, overlap_chars: int = 180) -> list[dict]:
    """Build chunks without losing page, sheet, row, or source offset metadata."""
    rows = []
    ordinal = 0
    for section, text, metadata in extract_sections(content, filename):
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


async def read_document_range(db, document_id: str, revision: int, offset: int, limit: int) -> tuple[str, int]:
    """Read a global text range from the persisted versioned chunks without reparsing the file."""
    section_rows = (await db.execute(
        select(
            DocumentChunk.section,
            func.min(DocumentChunk.ordinal).label('first_ordinal'),
            func.max(DocumentChunk.end_offset).label('section_length'),
        ).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.revision == revision,
        ).group_by(DocumentChunk.section).order_by(func.min(DocumentChunk.ordinal))
    )).all()
    if not section_rows:
        return '', 0

    sections = [(row.section, int(row.section_length or 0)) for row in section_rows]
    total_length = sum(
        (len(f'## {section}\n') if section else 0) + length
        for section, length in sections
    ) + max(0, len(sections) - 1) * 2
    requested_end = min(offset + limit, total_length)
    if requested_end <= offset:
        return '', total_length

    output = []
    cursor = 0
    for section_index, (section, section_length) in enumerate(sections):
        header = f'## {section}\n' if section else ''
        header_end = cursor + len(header)
        overlap_start = max(offset, cursor)
        overlap_end = min(requested_end, header_end)
        if overlap_end > overlap_start:
            output.append(header[overlap_start - cursor:overlap_end - cursor])

        body_start = header_end
        body_end = body_start + section_length
        local_start = max(0, offset - body_start)
        local_end = min(section_length, requested_end - body_start)
        if local_end > local_start:
            chunks = list((await db.execute(select(DocumentChunk).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.revision == revision,
                DocumentChunk.section == section,
                DocumentChunk.start_offset < local_end,
                DocumentChunk.end_offset > local_start,
            ).order_by(DocumentChunk.start_offset))).scalars().all())
            section_cursor = local_start
            for chunk in chunks:
                start = max(section_cursor, local_start, chunk.start_offset)
                end = min(local_end, chunk.end_offset)
                if end > start:
                    output.append(chunk.content[start - chunk.start_offset:end - chunk.start_offset])
                    section_cursor = end

        cursor = body_end
        if section_index < len(sections) - 1:
            separator_end = cursor + 2
            overlap_start = max(offset, cursor)
            overlap_end = min(requested_end, separator_end)
            if overlap_end > overlap_start:
                output.append('\n\n'[overlap_start - cursor:overlap_end - cursor])
            cursor = separator_end

    return ''.join(output), total_length


def document_range_evidence(
    document_id: str, filename: str, revision: int, offset: int, text: str, total_characters: int,
) -> dict:
    """Build a page result from the exact global range returned by the chunk reader."""
    end = min(offset + len(text), total_characters)
    evidence_id = hashlib.sha256(f'{document_id}:{revision}:{offset}:{end}'.encode()).hexdigest()[:24]
    return {
        'document_id': document_id,
        'filename': filename,
        'revision': revision,
        'offset': offset,
        'next_offset': end if end < total_characters else None,
        'total_characters': total_characters,
        'evidence_id': evidence_id,
        'text': text,
    }


async def search_document_chunks(
    db, document_id: str, revision: int, query: str, limit: int = 8
) -> list[DocumentChunk]:
    """Use OR-based PostgreSQL FTS + trigram or weighted lexical fallback in slim DBs."""
    if not query.strip():
        return []
    raw_terms = [term.casefold() for term in re.findall(r'\w+', query) if len(term) > 1]
    terms = list(dict.fromkeys(term for term in raw_terms if term not in _SEARCH_STOPWORDS))[:24]
    if not terms:
        terms = list(dict.fromkeys(raw_terms))[:24]
    if not terms:
        return []
    search_query = ' '.join(terms)
    base = select(DocumentChunk).where(
        DocumentChunk.document_id == document_id, DocumentChunk.revision == revision
    )
    if db.bind.dialect.name == 'postgresql':
        tsquery = func.to_tsquery('simple', ' | '.join(terms))
        vector = func.to_tsvector('simple', DocumentChunk.content)
        fts = vector.bool_op('@@')(tsquery)
        rank = func.ts_rank(vector, tsquery)
        similarity = func.similarity(DocumentChunk.content, search_query)
        fuzzy = DocumentChunk.content.op('%')(search_query)
        statement = base.where(or_(fts, fuzzy)).order_by((rank + similarity).desc()).limit(limit)
        return list((await db.execute(statement)).scalars().all())

    chunks = list((await db.execute(base)).scalars().all())
    document_frequency = Counter(
        term
        for chunk in chunks
        for term in set(re.findall(r'\w+', chunk.content.casefold()))
        if term in terms
    )

    def term_weight(term):
        return 1.0 + math.log((len(chunks) + 1) / (document_frequency[term] + 1))

    def score(chunk):
        text = chunk.content.casefold()
        exact = sum(min(text.count(term), 2) * term_weight(term) for term in terms)
        candidates = re.findall(r'\w+', text)
        fuzzy = max((
            (SequenceMatcher(None, term, candidate).ratio(), term)
            for term in terms for candidate in candidates
        ), default=(0, ''))
        fuzzy_score = fuzzy[0] * term_weight(fuzzy[1]) if fuzzy[0] >= 0.82 else 0
        return exact * 2 + fuzzy_score

    return sorted((chunk for chunk in chunks if score(chunk) > 0), key=score, reverse=True)[:limit]
