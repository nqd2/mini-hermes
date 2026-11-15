"""Observe current V1 parser behavior on synthetic fixtures, not a V2 contract."""

from pathlib import Path

from open_webui.mini_hermes.parsers import extract_sections

FIXTURES = Path(__file__).parent / 'fixtures' / 'v2'


def extract(name):
    return extract_sections((FIXTURES / name).read_bytes(), name)


def test_current_docx_parser_extracts_body_table_but_not_header_footer():
    sections = extract('docx-f01-structured.docx')
    assert sections == [(
        'document',
        'Synthetic interview: opening paragraph.\nLink: external reference\nQuestion\tMultiline\nanswer',
        {},
    )]


def test_current_pptx_parser_extracts_top_level_text_but_not_grouped_shape():
    assert extract('pptx-f01-structured.pptx') == [
        ('slide 1', 'Synthetic slide one\nPlaceholder content', {'slide': 1}),
        ('slide 2', 'Second slide text', {'slide': 2}),
    ]


def test_current_pdf_parser_returns_empty_text_without_image_coverage_metadata():
    assert extract('pdf-f01-text-and-image.pdf') == [
        ('page 1', 'Synthetic selectable text layer\n', {'page': 1}),
        ('page 2', '', {'page': 2}),
    ]
