"""Tests for DOCX Canonical Engine and OpenXML RunSpanMatcher (M2.3)."""

import io
from pathlib import Path

import docx
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from weai.persistence.v2.canonical.document import CanonicalDocumentModel
from weai.persistence.v2.parsers.docx import (
    DocxFormatError,
    DocxParser,
    RunSpanMatcher,
    TextNotFoundError,
    UnsupportedContentError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


@pytest.fixture
def f01_docx_bytes() -> bytes:
    return (FIXTURES_DIR / "docx-f01-structured.docx").read_bytes()


def test_docx_canonical_extraction(f01_docx_bytes):
    """Verifies complete parsing of sections, headers, footers, tables, and hyperlinks."""
    model = DocxParser.parse(f01_docx_bytes)
    assert isinstance(model, CanonicalDocumentModel)
    assert len(model.sections) > 0

    section = model.sections[0]
    assert section.header_text is not None and "Synthetic header" in section.header_text
    assert section.footer_text is not None and "Synthetic footer" in section.footer_text

    # Tables extraction
    assert len(section.tables) > 0
    t = section.tables[0]
    assert any("Multiline" in cell for row in t.cells_matrix for cell in row)

    # Paragraphs and Hyperlinks
    all_paras = model.all_paragraphs
    assert len(all_paras) > 0

    found_hyperlink = False
    for p in all_paras:
        if p.hyperlinks:
            for h in p.hyperlinks:
                if "https://example.invalid/synthetic" in str(h.get("target")):
                    found_hyperlink = True
                    break
    assert found_hyperlink, "Expected synthetic external hyperlink in docx-f01"


def test_docx_run_span_matcher_single_run():
    """Text replacement within a single run preserves formatting and prefixes/suffixes."""
    doc = docx.Document()
    p = doc.add_paragraph()
    r = p.add_run("Hello target world")
    r.bold = True
    r.italic = True

    RunSpanMatcher.replace_in_paragraph(p, "target", "magnificent")
    assert p.text == "Hello magnificent world"
    assert len(p.runs) == 1
    assert p.runs[0].bold is True
    assert p.runs[0].italic is True


def test_docx_run_span_matcher_multi_run_span():
    """Text replacement crossing multi-run boundaries preserves opening style and closing suffix."""
    doc = docx.Document()
    p = doc.add_paragraph()
    r1 = p.add_run("The ")
    r2 = p.add_run("quick ")
    r2.bold = True
    r3 = p.add_run("brown fox ")
    r3.italic = True
    r4 = p.add_run("jumps.")

    # Target text spans across r2 and r3: "quick brown fox "
    RunSpanMatcher.replace_in_paragraph(p, "quick brown fox ", "lazy dog ")

    assert p.text == "The lazy dog jumps."
    # r1 is untouched
    assert r1.text == "The "
    # r2 received replacement and kept bold
    assert r2.text == "lazy dog "
    assert r2.bold is True
    # r3 was consumed and cleared
    assert r3.text == ""
    # r4 suffix was untouched
    assert r4.text == "jumps."


def test_docx_run_span_matcher_tracked_changes_rejected():
    """Paragraphs containing tracked changes (<w:ins>, <w:del>) fail-closed."""
    doc = docx.Document()
    p = doc.add_paragraph()
    p.add_run("Normal text ")

    # Inject tracked revision XML tag
    ins_elm = OxmlElement("w:ins")
    p._p.append(ins_elm)

    with pytest.raises(UnsupportedContentError) as exc_info:
        RunSpanMatcher.replace_in_paragraph(p, "Normal", "Replaced")
    assert exc_info.value.status_code == 422
    assert "tracked changes" in str(exc_info.value).lower()


def test_docx_run_span_matcher_complex_field_rejected():
    """Paragraphs containing complex Word fields (<w:fldSimple>) fail-closed."""
    doc = docx.Document()
    p = doc.add_paragraph()
    p.add_run("Field text ")

    # Inject complex field XML tag
    fld_elm = OxmlElement("w:fldSimple")
    p._p.append(fld_elm)

    with pytest.raises(UnsupportedContentError) as exc_info:
        RunSpanMatcher.replace_in_paragraph(p, "Field", "Replaced")
    assert exc_info.value.status_code == 422
    assert "complex word fields" in str(exc_info.value).lower()


def test_docx_run_span_matcher_hyperlink_crossing_rejected(f01_docx_bytes):
    """Edits intersecting a hyperlink boundary fail-closed."""
    doc = docx.Document(io.BytesIO(f01_docx_bytes))
    # Find paragraph with hyperlink
    target_p = None
    for p in doc.paragraphs:
        if p._p.xpath(".//w:hyperlink"):
            target_p = p
            break
    if not target_p:
        pytest.skip("No hyperlink paragraph found in fixture")

    # Attempting to replace text intersecting hyperlink
    with pytest.raises(UnsupportedContentError):
        RunSpanMatcher.replace_in_paragraph(target_p, "reference", "replacement")


def test_docx_run_span_matcher_text_not_found():
    """Non-matching search string raises TextNotFoundError."""
    doc = docx.Document()
    p = doc.add_paragraph("Some existing sentence.")
    with pytest.raises(TextNotFoundError):
        RunSpanMatcher.replace_in_paragraph(p, "missing phrase", "replacement")


def test_docx_apply_edit_integration(f01_docx_bytes):
    """DocxParser.apply_edit modifies target paragraph and produces valid docx bytes."""
    doc = docx.Document(io.BytesIO(f01_docx_bytes))
    target_idx = 1
    original_text = doc.paragraphs[0].text
    words = original_text.split()
    if len(words) < 2:
        pytest.skip("First paragraph too short in fixture")

    target_word = words[0]
    replacement = "REPLACED_WORD"

    modified_bytes = DocxParser.apply_edit(
        content=f01_docx_bytes,
        paragraph_order=1,
        target_text=target_word,
        replacement_text=replacement,
        occurrence=1,
    )
    assert modified_bytes is not None

    # Parse back and verify
    mod_doc = docx.Document(io.BytesIO(modified_bytes))
    assert replacement in mod_doc.paragraphs[0].text
    assert target_word not in mod_doc.paragraphs[0].text.split()[0]


def test_docx_projection_to_db_records(f01_docx_bytes):
    """to_db_records projects model into Domain 2 table rows."""
    import uuid

    model = DocxParser.parse(f01_docx_bytes)
    build_id = uuid.uuid4()
    sec_rows, para_rows = model.to_db_records(build_id)

    assert len(sec_rows) == len(model.sections)
    assert len(para_rows) == len(model.all_paragraphs)

    for s in sec_rows:
        assert s["index_build_id"] == build_id
        assert "section_order" in s

    for p in para_rows:
        assert "paragraph_order" in p
        assert "raw_text" in p
        assert "runs_json" in p
