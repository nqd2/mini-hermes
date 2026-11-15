"""Verify synthetic fixtures themselves; these tests do not validate V2 parsing."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfReader

FIXTURES = Path(__file__).parent / 'fixtures' / 'v2'


def test_new_format_fixture_checksums_match_manifest():
    manifest = dict(line.split(maxsplit=1)[::-1] for line in (FIXTURES / 'SHA256SUMS.txt').read_text().splitlines())
    for name in ('docx-f01-structured.docx', 'pptx-f01-structured.pptx', 'pdf-f01-text-and-image.pdf'):
        assert sha256((FIXTURES / name).read_bytes()).hexdigest() == manifest[name]


def test_docx_contains_body_table_header_footer_and_hyperlink():
    path = FIXTURES / 'docx-f01-structured.docx'
    doc = Document(path)
    assert doc.paragraphs[0].text == 'Synthetic interview: opening paragraph.'
    assert doc.tables[0].cell(0, 1).text == 'Multiline\nanswer'
    assert doc.sections[0].header.paragraphs[0].text == 'Synthetic header'
    assert doc.sections[0].footer.paragraphs[0].text == 'Synthetic footer'
    with ZipFile(path) as archive:
        assert b'external reference' in archive.read('word/document.xml')
        assert b'https://example.invalid/synthetic' in archive.read('word/_rels/document.xml.rels')


def test_pptx_contains_multiple_slides_placeholder_and_grouped_text():
    presentation = Presentation(FIXTURES / 'pptx-f01-structured.pptx')
    assert len(presentation.slides) == 2
    assert presentation.slides[0].shapes.title.text == 'Synthetic slide one'
    assert presentation.slides[0].placeholders[1].text == 'Placeholder content'
    group = next(shape for shape in presentation.slides[0].shapes if shape.shape_type == MSO_SHAPE_TYPE.GROUP)
    assert group.shapes[0].text_frame.text == 'Grouped content'
    assert presentation.slides[1].shapes[0].text_frame.text == 'Second slide text'


def test_pdf_has_text_page_and_image_only_page():
    document = PdfReader(FIXTURES / 'pdf-f01-text-and-image.pdf')
    assert len(document.pages) == 2
    assert 'Synthetic selectable text layer' in document.pages[0].extract_text()
    assert document.pages[1].extract_text().strip() == ''
    resources = document.pages[1]['/Resources']['/XObject'].get_object()
    assert any(obj.get_object()['/Subtype'] == '/Image' for obj in resources.values())
