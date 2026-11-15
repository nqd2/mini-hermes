from io import BytesIO

import pytest
from openpyxl import Workbook


def test_workbook_survey_keeps_record_counts_distinct_from_unique_codes():
    from open_webui.mini_hermes.document_analysis import survey_document

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Programs', 'Program Code', 'Teaching Language', 'Basic Study Duration(Y)'])
    sheet.append(['Engineering', '001234', 'English', 3])
    sheet.append([None, 'Supervisor Name'])
    sheet.append(['Programs', 'Program Code', 'Teaching Language', 'Basic Study Duration(Y)'])
    sheet.append(['Engineering', 1234, 'Chinese', None])
    sheet['B5'].number_format = '000000'
    sheet.append(['Notes', '=1+1'])
    sheet.merge_cells('A7:B7')
    stream = BytesIO()
    workbook.save(stream)
    survey = survey_document(stream.getvalue(), 'test.xlsx')
    profile = survey.profile['sheets'][0]
    assert profile['records'] == 2
    assert profile['unique_keys'] == 1
    assert profile['columns']['language']['counts'] == {'English': 1, 'Chinese': 1}
    assert profile['columns']['duration']['missing'] == 1
    assert profile['merged_ranges'] == ['A7:B7']
    assert profile['formulas']['missing_cache'] == 1
    assert any('A5' in unit['text'] and '001234' in unit['text'] for unit in survey.units)


def test_word_survey_preserves_table_order():
    from docx import Document
    from open_webui.mini_hermes.document_analysis import survey_document

    document = Document()
    document.add_heading('First', level=1)
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'Middle'
    document.add_paragraph('Last')
    stream = BytesIO()
    document.save(stream)
    survey = survey_document(stream.getvalue(), 'test.docx')
    assert [unit['text'] for unit in survey.units] == ['First', 'Middle', 'Last']
    assert [unit['kind'] for unit in survey.units] == ['heading', 'table', 'paragraph']


def test_pdf_marks_image_only_pages_for_ocr():
    from pathlib import Path
    from PIL import Image
    from open_webui.mini_hermes.document_analysis import survey_document

    stream = BytesIO()
    Image.new('RGB', (32, 32), 'white').save(stream, format='PDF')
    survey = survey_document(stream.getvalue(), 'scan.pdf')
    assert survey.profile['pages'] == 1
    assert [(unit['kind'], unit['locator'], unit['page']) for unit in survey.units] == [('ocr', 'page/1', 1)]
    mixed = Path(__file__).parent / 'fixtures/v2/pdf-f01-text-and-image.pdf'
    assert any(unit['kind'] == 'text' for unit in survey_document(mixed.read_bytes(), 'mixed.pdf').units)


def test_slide_survey_preserves_tables_and_identifies_embedded_images():
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches
    from open_webui.mini_hermes.document_analysis import survey_document

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_table(1, 1, 0, 0, Inches(2), Inches(1)).table.cell(0, 0).text = 'Revenue'
    image = BytesIO()
    Image.new('RGB', (32, 32), 'white').save(image, format='PNG')
    slide.shapes.add_picture(image, 0, Inches(1))
    stream = BytesIO()
    deck.save(stream)
    survey = survey_document(stream.getvalue(), 'test.pptx')
    assert survey.profile['slides'] == 1
    assert survey.units[0]['locator'] == 'slide/1/shape/1/row/1'
    assert survey.units[0]['text'] == 'Revenue'
    assert survey.units[1]['locator'] == 'slide/1/shape/2/image/1'
    assert survey.units[1]['asset'].startswith('ppt/media/')


def test_chat_summary_intent_does_not_capture_selected_pages():
    from open_webui.mini_hermes.agent_mode import document_agent_mode
    from open_webui.mini_hermes.document_analysis import wants_document_summary

    assert wants_document_summary('tóm tắt file đi')
    assert wants_document_summary('Summarize M.xlsx in English')
    assert document_agent_mode([{'role': 'user', 'content': 'Summarize M.xlsx'}], has_files=True)
    assert not wants_document_summary('Tóm tắt trang 3 của file này')
    assert not wants_document_summary('Summarize sheet Sales')
    assert not wants_document_summary('Summarize the first page only')
    assert not wants_document_summary('Which sheet has 10 rows?')


def test_declared_excel_table_counts_records_and_blank_values_without_a_program_header():
    from openpyxl.drawing.image import Image as ExcelImage
    from openpyxl.worksheet.table import Table
    from PIL import Image
    from open_webui.mini_hermes.document_analysis import survey_document

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Salesperson', 'Region'])
    sheet.append(['Alex', 'North'])
    sheet.append(['Sam', None])
    sheet.add_table(Table(displayName='Sales', ref='A1:B3'))
    picture = BytesIO()
    Image.new('RGB', (24, 24), 'white').save(picture, format='PNG')
    picture.seek(0)
    sheet.add_image(ExcelImage(picture), 'D4')
    stream = BytesIO()
    workbook.save(stream)
    survey = survey_document(stream.getvalue(), 'sales.xlsx')
    table = survey.profile['sheets'][0]['tables'][0]
    assert table['records'] == 2
    assert table['columns'][1] == {'name': 'Region', 'missing': 1, 'distinct': 1, 'counts': {'North': 1}}
    assert any(unit['kind'] == 'ocr' and '!D4/' in unit['locator'] for unit in survey.units)


def test_word_keeps_text_boxes_as_separate_evidence():
    from docx import Document
    from docx.oxml import parse_xml
    from open_webui.mini_hermes.document_analysis import survey_document

    document = Document()
    paragraph = document.add_paragraph('Ordinary text')
    paragraph._p.append(parse_xml(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:pict><w:txbxContent><w:p><w:r><w:t>Important exception</w:t></w:r></w:p>'
        '</w:txbxContent></w:pict></w:r>'
    ))
    stream = BytesIO()
    document.save(stream)
    result = survey_document(stream.getvalue(), 'test.docx')
    assert [(unit['kind'], unit['text']) for unit in result.units] == [
        ('paragraph', 'Ordinary text'), ('text_box', 'Important exception'),
    ]


@pytest.mark.asyncio
async def test_analysis_worker_rejects_citations_outside_the_current_batch():
    from open_webui.mini_hermes.document_analysis import Survey
    from open_webui.mini_hermes.document_analysis_tasks import summarize_analysis_batch

    survey = Survey()
    survey.add('paragraph', 'block/1', 'Known text')
    survey.add('table', 'block/2/row/1', 'Known table')
    calls = []

    async def provider(messages, **kwargs):
        calls.append(messages)
        return '- Verified point [E1]\n- Verified table [E2]\n- Unknown [E99]'

    batch = await summarize_analysis_batch(survey.units, 0, 'Tóm tắt', provider)
    assert len(calls) == 1
    assert 'Known text' in calls[0][-1]['content']
    assert batch == '- Verified point [E1]\n- Verified table [E2]'
