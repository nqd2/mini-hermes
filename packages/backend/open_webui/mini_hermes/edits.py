"""Constrained Office edits shared by previews and committed revisions."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


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

        book = load_workbook(BytesIO(content), read_only=True, data_only=False)
        return '\n'.join(f'{sheet.title}!{row[0].coordinate}: ' + '\t'.join('' if c.value is None else str(c.value) for c in row)
                         for sheet in book for row in sheet.rows if row)
    if suffix == '.pptx':
        from pptx import Presentation

        deck = Presentation(BytesIO(content))
        return '\n'.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
    if suffix == '.pdf':
        from pypdf import PdfReader

        return '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(content)).pages)
    raise ValueError(f'Unsupported file type: {suffix}')
