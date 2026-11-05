from io import BytesIO
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from docx import Document
from openpyxl import Workbook, load_workbook
from pptx import Presentation

spec = spec_from_file_location('document_edits', Path(__file__).parents[1] / 'open_webui' / 'mini_hermes' / 'edits.py')
edits = module_from_spec(spec)
spec.loader.exec_module(edits)
apply_operations = edits.apply_operations
extract_text = edits.extract_text


def test_docx_content_structure_and_format():
    document = Document()
    document.add_paragraph('Before')
    source = BytesIO()
    document.save(source)
    result = apply_operations(source.getvalue(), 'report.docx', [
        {'op': 'replace_text', 'find': 'Before', 'replace': 'After'},
        {'op': 'append_paragraph', 'text': 'New section', 'style': 'Title'},
    ])
    edited = Document(BytesIO(result))
    assert [p.text for p in edited.paragraphs] == ['After', 'New section']
    assert edited.paragraphs[1].style.name == 'Title'


def test_xlsx_cell_and_style():
    workbook = Workbook()
    source = BytesIO()
    workbook.save(source)
    result = apply_operations(source.getvalue(), 'data.xlsx', [
        {'op': 'set_cell', 'sheet': 'Sheet', 'cell': 'B2', 'value': '=1+2', 'number_format': '0.00'},
        {'op': 'add_sheet', 'name': 'Summary'},
    ])
    edited = load_workbook(BytesIO(result))
    assert edited['Sheet']['B2'].value == '=1+2'
    assert edited['Sheet']['B2'].number_format == '0.00'
    assert 'Summary' in edited.sheetnames


def test_pptx_text_and_structure():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, 1000000, 1000000)
    box.text = 'Before'
    source = BytesIO()
    deck.save(source)
    result = apply_operations(source.getvalue(), 'slides.pptx', [
        {'op': 'replace_text', 'find': 'Before', 'replace': 'After'},
        {'op': 'add_slide', 'layout': 6},
    ])
    edited = Presentation(BytesIO(result))
    assert edited.slides[0].shapes[0].text == 'After'
    assert len(edited.slides) == 2


def test_pdf_cannot_be_edited():
    try:
        apply_operations(b'%PDF-1.4', 'read.pdf', [{'op': 'replace_text', 'find': 'x', 'replace': 'y'}])
    except ValueError as exc:
        assert 'read-only' in str(exc)
    else:
        raise AssertionError('PDF mutation should fail')


def test_extract_text_includes_docx_tables_and_zero_cells():
    document = Document()
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'Table value'
    source = BytesIO()
    document.save(source)
    assert 'Table value' in extract_text(source.getvalue(), 'report.docx')

    workbook = Workbook()
    workbook.active['A1'] = 0
    source = BytesIO()
    workbook.save(source)
    assert 'Sheet!A1: 0' in extract_text(source.getvalue(), 'data.xlsx')


def test_extract_text_handles_sparse_rows_in_read_only_xlsx():
    workbook = Workbook()
    workbook.active.title = 'Schedule'
    workbook.active['C2'] = 'Internship'
    workbook.active['E2'] = 0
    source = BytesIO()
    workbook.save(source)

    text = extract_text(source.getvalue(), 'schedule.xlsx')

    assert 'Internship' in text
    assert '\t0' in text
