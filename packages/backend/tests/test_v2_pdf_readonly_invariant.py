"""Tests for PDF Parser, Text/Image Page Classification, and Read-Only Invariant (M2.4)."""

import io
from pathlib import Path

import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image

from weai.persistence.v2.parsers.pdf import (
    FormatEditUnsupportedError,
    PageLayerType,
    PdfParser,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


@pytest.fixture
def f01_pdf_bytes() -> bytes:
    return (FIXTURES_DIR / "pdf-f01-text-and-image.pdf").read_bytes()


def test_pdf_parser_classifies_text_and_image_pages(f01_pdf_bytes):
    """Verifies PDF page extraction and accurate classification into TEXT_LAYER vs IMAGE_ONLY."""
    model = PdfParser.parse(f01_pdf_bytes)
    assert model.num_pages == 2

    page1 = model.pages[0]
    page2 = model.pages[1]

    # Page 1: Selectable text layer
    assert page1.page_number == 1
    assert page1.layer_type == PageLayerType.TEXT_LAYER
    assert "Synthetic selectable text layer" in page1.raw_text

    # Page 2: Raster image only, zero text
    assert page2.page_number == 2
    assert page2.layer_type == PageLayerType.IMAGE_ONLY
    assert page2.raw_text == ""
    assert page2.num_images >= 1

    assert model.has_scanned_pages is True


def test_pdf_propose_edit_raises_422():
    """Unconditionally rejects edit proposals on PDF documents with HTTP 422."""
    with pytest.raises(FormatEditUnsupportedError) as exc_info:
        PdfParser.propose_edit(document_id="dummy", operations=[])

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "format_edit_unsupported"
    assert exc_info.value.detail["format"] == "pdf"
    assert exc_info.value.detail["supported_operations"] == []


def test_pdf_apply_mutation_raises_422():
    """Unconditionally rejects mutation applications on PDF documents with HTTP 422."""
    with pytest.raises(FormatEditUnsupportedError) as exc_info:
        PdfParser.apply_mutation(document_id="dummy")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "format_edit_unsupported"


def test_pdf_apply_edit_raises_422():
    """Unconditionally rejects apply_edit on PDF documents with HTTP 422."""
    with pytest.raises(FormatEditUnsupportedError) as exc_info:
        PdfParser.apply_edit(b"data", 1, "target", "replacement")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "format_edit_unsupported"


def test_pdf_pure_scanned_document_classification():
    """Generates a pure scanned PDF with 2 image-only pages and verifies zero text layer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    for p_idx in range(2):
        img = Image.new("RGB", (100, 100), color=(200, p_idx * 100, 50))
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        img_buf.seek(0)
        c.drawImage(ImageReader(img_buf), 50, 400, width=400, height=300)
        c.showPage()
    c.save()

    scanned_bytes = buf.getvalue()
    model = PdfParser.parse(scanned_bytes)
    assert model.num_pages == 2
    for p in model.pages:
        assert p.layer_type == PageLayerType.IMAGE_ONLY
        assert p.raw_text == ""
        assert p.num_images >= 1
    assert model.has_scanned_pages is True
