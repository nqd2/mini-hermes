"""Constrained Office edits shared by previews and committed revisions."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


_OPERATION_ALIASES = {
    'replace': 'replace_text',
    'replace_all': 'replace_text',
    'find_replace': 'replace_text',
    'search_replace': 'replace_text',
    'text_replace': 'replace_text',
    'replace_text_in_document': 'replace_text',
    'append_text': 'append_paragraph',
    'set_cell_value': 'set_cell',
    'update_cell': 'set_cell',
}
_SUPPORTED_OPERATIONS = {
    'replace_text', 'append_paragraph', 'set_paragraph_style', 'set_table_cell',
    'set_cell', 'add_sheet', 'remove_sheet', 'append_row', 'set_shape_text',
    'set_shape_fill', 'add_slide',
}


def normalize_operations(operations: list[dict]) -> list[dict]:
    """Normalize common LLM operation aliases into the editor's canonical schema."""
    if not isinstance(operations, list):
        raise ValueError('operations must be a list')
    normalized = []
    for item in operations:
        if not isinstance(item, dict):
            raise ValueError('Each document operation must be an object')
        op = dict(item)
        nested = op.get('operation')
        if isinstance(nested, dict):
            op = {**nested, **{key: value for key, value in op.items() if key != 'operation'}}

        raw_kind = op.get('op') or op.get('action') or op.get('operation') or op.get('type')
        if not raw_kind:
            raw_kind = next((key for key in _SUPPORTED_OPERATIONS if key in op), None)
            if raw_kind:
                payload = op.get(raw_kind)
                if isinstance(payload, dict):
                    op = {**payload, **{key: value for key, value in op.items() if key != raw_kind}}

        if not isinstance(raw_kind, str) or not raw_kind.strip():
            raise ValueError('Each document operation must include an op/action/type name')
        kind = raw_kind.strip().lower().replace('-', '_').replace(' ', '_')
        kind = _OPERATION_ALIASES.get(kind, kind)

        if kind == 'replace_text':
            find = next((op[key] for key in ('find', 'find_text', 'search', 'old_text', 'old') if key in op), None)
            replacement = next((op[key] for key in ('replace', 'replacement', 'replace_with', 'new_text', 'new', 'value', 'content') if key in op), None)
            if not isinstance(find, str) or not isinstance(replacement, str):
                raise ValueError('replace_text requires string fields "find" and "replace"')
            op.update({'find': find, 'replace': replacement})
        elif kind in {'append_paragraph', 'set_table_cell', 'set_shape_text'}:
            if 'text' not in op:
                text = next((op[key] for key in ('content', 'value', 'new_text') if key in op), None)
                if isinstance(text, str):
                    op['text'] = text
        elif kind == 'set_cell' and 'value' not in op:
            if 'content' in op:
                op['value'] = op['content']

        op['op'] = kind
        op.pop('action', None)
        op.pop('type', None)
        normalized.append(op)
    return normalized


def _replace_runs(paragraph, old: str, new: str) -> None:
    # Preserve formatting when the match sits inside a run. Cross-run matches
    # retain paragraph style but use the first run's character style.
    for run in paragraph.runs:
        run.text = run.text.replace(old, new)
    if old in paragraph.text:
        first = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
        first.text = paragraph.text.replace(old, new)
        for run in paragraph.runs[1:]:
            run.text = ''


def apply_operations(content: bytes, filename: str, operations: list[dict]) -> bytes:
    operations = normalize_operations(operations)
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf':
        raise ValueError('PDF is read-only')
    if suffix == '.docx':
        from docx import Document

        doc = Document(BytesIO(content))
        for op in operations:
            kind = op.get('op')
            if kind == 'replace_text':
                for paragraph in doc.paragraphs:
                    _replace_runs(paragraph, op['find'], op['replace'])
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            for paragraph in cell.paragraphs:
                                _replace_runs(paragraph, op['find'], op['replace'])
            elif kind == 'append_paragraph':
                doc.add_paragraph(op['text'], style=op.get('style'))
            elif kind == 'set_paragraph_style':
                doc.paragraphs[op['index']].style = op['style']
            elif kind == 'set_table_cell':
                doc.tables[op['table']].cell(op['row'], op['column']).text = op['text']
            else:
                raise ValueError(f'Unsupported DOCX operation: {kind}')
        out = BytesIO()
        doc.save(out)
        return out.getvalue()
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill

        book = load_workbook(BytesIO(content))
        for op in operations:
            kind = op.get('op')
            if kind == 'set_cell':
                cell = book[op['sheet']][op['cell']]
                cell.value = op.get('value')
                if 'number_format' in op:
                    cell.number_format = op['number_format']
                if 'bold' in op:
                    cell.font = Font(name=cell.font.name, size=cell.font.sz, bold=op['bold'])
                if 'fill' in op:
                    cell.fill = PatternFill(fill_type='solid', fgColor=op['fill'])
            elif kind == 'add_sheet':
                book.create_sheet(op['name'])
            elif kind == 'remove_sheet':
                del book[op['name']]
            elif kind == 'append_row':
                book[op['sheet']].append(op['values'])
            else:
                raise ValueError(f'Unsupported XLSX operation: {kind}')
        out = BytesIO()
        book.save(out)
        return out.getvalue()
    if suffix == '.pptx':
        from pptx import Presentation
        from pptx.dml.color import RGBColor

        deck = Presentation(BytesIO(content))
        for op in operations:
            kind = op.get('op')
            if kind == 'replace_text':
                for slide in deck.slides:
                    for shape in slide.shapes:
                        if shape.has_text_frame:
                            for paragraph in shape.text_frame.paragraphs:
                                _replace_runs(paragraph, op['find'], op['replace'])
            elif kind == 'set_shape_text':
                deck.slides[op['slide']].shapes[op['shape']].text = op['text']
            elif kind == 'set_shape_fill':
                shape = deck.slides[op['slide']].shapes[op['shape']]
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(op['color'].lstrip('#'))
            elif kind == 'add_slide':
                deck.slides.add_slide(deck.slide_layouts[op.get('layout', 6)])
            else:
                raise ValueError(f'Unsupported PPTX operation: {kind}')
        out = BytesIO()
        deck.save(out)
        return out.getvalue()
    raise ValueError(f'Unsupported file type: {suffix}')


def extract_text(content: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == '.docx':
        from docx import Document

        doc = Document(BytesIO(content))
        lines = [p.text for p in doc.paragraphs]
        lines.extend('\t'.join(cell.text for cell in row.cells)
                     for table in doc.tables for row in table.rows)
        return '\n'.join(lines)
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        book = load_workbook(BytesIO(content), read_only=True, data_only=False)
        try:
            lines = []
            for sheet in book:
                first_column = get_column_letter(sheet.min_column or 1)
                for row_number, row in enumerate(sheet.iter_rows(), start=sheet.min_row or 1):
                    if not row:
                        continue
                    values = '\t'.join('' if cell.value is None else str(cell.value) for cell in row)
                    lines.append(f'{sheet.title}!{first_column}{row_number}: {values}')
            return '\n'.join(lines)
        finally:
            book.close()
    if suffix == '.pptx':
        from pptx import Presentation

        deck = Presentation(BytesIO(content))
        return '\n'.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
    if suffix == '.pdf':
        from pypdf import PdfReader

        return '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(content)).pages)
    raise ValueError(f'Unsupported file type: {suffix}')
