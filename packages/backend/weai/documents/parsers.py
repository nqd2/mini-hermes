"""Format-specific resource parsers selected through a strategy registry."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path

Section = tuple[str, str, dict]
SectionParser = Callable[[bytes, str], list[Section]]


def _delimited_sections(content: bytes, _filename: str, delimiter: str) -> list[Section]:
    from openpyxl.utils import get_column_letter

    rows = []
    reader = csv.reader(StringIO(content.decode('utf-8-sig', errors='replace'), newline=''), delimiter=delimiter)
    for row_number, values in enumerate(reader, start=1):
        cells = [
            f'{get_column_letter(column)}{row_number}={value}'
            for column, value in enumerate(values, start=1)
            if value != ''
        ]
        if cells:
            rows.append(f'row {row_number}: ' + ' | '.join(cells))
    return [('Table', '\n'.join(rows), {'sheet': 'Table'})]


def _csv_sections(content: bytes, filename: str) -> list[Section]:
    return _delimited_sections(content, filename, ',')


def _tsv_sections(content: bytes, filename: str) -> list[Section]:
    return _delimited_sections(content, filename, '\t')


def _json_sections(content: bytes, _filename: str) -> list[Section]:
    try:
        value = json.loads(content.decode('utf-8-sig', errors='replace'))
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = content.decode('utf-8', errors='replace')
    return [('document', text, {'format': 'json'})]


def _xlsx_sections(content: bytes, _filename: str) -> list[Section]:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
    cached_workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    result = []
    try:
        for sheet in workbook:
            cached_sheet = cached_workbook[sheet.title]
            lines = []
            first_row = sheet.min_row or 1
            for row_number, row in enumerate(sheet.iter_rows(), start=first_row):
                values = []
                for cell in row:
                    if cell.value is None:
                        continue
                    rendered = f'{cell.coordinate}={cell.value}'
                    if isinstance(cell.value, str) and cell.value.startswith('='):
                        cached_value = cached_sheet[cell.coordinate].value
                        rendered += f' [cached={cached_value}]'
                    values.append(rendered)
                if values:
                    lines.append(f'row {row_number}: ' + ' | '.join(values))
            result.append(
                (
                    sheet.title,
                    '\n'.join(lines),
                    {
                        'sheet': sheet.title,
                        'first_column': get_column_letter(sheet.min_column or 1),
                        'last_column': get_column_letter(sheet.max_column or 1),
                        'first_row': first_row,
                        'last_row': sheet.max_row or first_row,
                    },
                )
            )
    finally:
        workbook.close()
        cached_workbook.close()
    return result


def _pdf_sections(content: bytes, _filename: str) -> list[Section]:
    from pypdf import PdfReader

    return [
        (f'page {index}', page.extract_text() or '', {'page': index})
        for index, page in enumerate(PdfReader(BytesIO(content)).pages, start=1)
    ]


def _pptx_sections(content: bytes, _filename: str) -> list[Section]:
    from pptx import Presentation

    deck = Presentation(BytesIO(content))
    return [
        (f'slide {index}', '\n'.join(shape.text for shape in slide.shapes if shape.has_text_frame), {'slide': index})
        for index, slide in enumerate(deck.slides, start=1)
    ]


def _docx_sections(content: bytes, _filename: str) -> list[Section]:
    from docx import Document as WordDocument

    document = WordDocument(BytesIO(content))
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    paragraphs.extend('\t'.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows)
    return [('document', '\n'.join(paragraphs), {})]


def _text_sections(content: bytes, _filename: str) -> list[Section]:
    return [('document', content.decode('utf-8', errors='replace'), {})]


_SECTION_PARSERS: dict[str, SectionParser] = {
    '.csv': _csv_sections,
    '.tsv': _tsv_sections,
    '.json': _json_sections,
    '.xlsx': _xlsx_sections,
    '.pdf': _pdf_sections,
    '.pptx': _pptx_sections,
    '.docx': _docx_sections,
}


def extract_sections(content: bytes, filename: str) -> list[Section]:
    """Parse a resource into structured sections while preserving source metadata."""
    suffix = Path(filename).suffix.lower()
    parser = _SECTION_PARSERS.get(suffix, _text_sections)
    return parser(content, filename)
