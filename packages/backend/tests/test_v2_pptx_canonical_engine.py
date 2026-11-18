"""Tests for PPTX Canonical Engine, Grouped Shape Traversal, and Shape Edit (M2.4)."""

import io
from pathlib import Path

import pytest

from weai.persistence.v2.canonical.presentation import (
    CanonicalPresentationModel,
    CanonicalShape,
)
from weai.persistence.v2.models.enums import ShapeType
from weai.persistence.v2.parsers.pptx import PptxFormatError, PptxParser

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


@pytest.fixture
def f01_pptx_bytes() -> bytes:
    return (FIXTURES_DIR / "pptx-f01-structured.pptx").read_bytes()


def test_pptx_slide_enumeration_and_order(f01_pptx_bytes):
    """Verifies slide ordering, layout names, and titles."""
    model = PptxParser.parse(f01_pptx_bytes)
    assert isinstance(model, CanonicalPresentationModel)
    assert model.slide_count == 2

    slide1 = model.slides[0]
    slide2 = model.slides[1]

    assert slide1.slide_order == 1
    assert slide2.slide_order == 2
    assert slide1.title is not None and "Synthetic slide one" in slide1.title


def test_pptx_grouped_shapes_recursive_traversal(f01_pptx_bytes):
    """Verifies recursive traversal finds nested shapes inside GroupShape containers."""
    model = PptxParser.parse(f01_pptx_bytes)
    slide1 = model.slides[0]

    # Find the group shape on slide 1
    group_shape = None
    for s in slide1.shapes:
        if s.is_group:
            group_shape = s
            break

    assert group_shape is not None, "Expected GroupShape on slide 1 in pptx-f01"
    assert group_shape.shape_type == ShapeType.GROUP
    assert len(group_shape.children) > 0

    # Verify that child shape inside group has text 'Grouped content'
    child_texts = [c.text_frame_content for c in group_shape.children if c.text_frame_content]
    assert any("Grouped content" in t for t in child_texts)

    # Verify all_shapes_flat includes both group and child shapes
    flat = slide1.all_shapes_flat
    assert len(flat) > len(slide1.shapes)
    assert any(s.shape_id == group_shape.shape_id for s in flat)
    for c in group_shape.children:
        assert any(s.shape_id == c.shape_id for s in flat)


def test_pptx_grouped_shape_edit_blocked(f01_pptx_bytes):
    """Direct edit on a GroupShape container must be rejected with 422."""
    model = PptxParser.parse(f01_pptx_bytes)
    slide1 = model.slides[0]
    group_shape = next(s for s in slide1.shapes if s.is_group)

    with pytest.raises(PptxFormatError) as exc_info:
        PptxParser.apply_edit(
            content=f01_pptx_bytes,
            slide_order=1,
            shape_id=group_shape.shape_id,
            target_text="Grouped",
            replacement_text="Replacement",
        )
    assert exc_info.value.status_code == 422
    assert "GroupShape container" in str(exc_info.value)


def test_pptx_leaf_shape_edit_succeeds(f01_pptx_bytes):
    """Editing a leaf text shape updates the text frame while preserving slide structure."""
    model = PptxParser.parse(f01_pptx_bytes)
    slide2 = model.slides[1]

    # Find text shape on slide 2
    target_shape = None
    for s in slide2.all_shapes_flat:
        if s.text_frame_content and "Second slide text" in s.text_frame_content:
            target_shape = s
            break
    assert target_shape is not None

    modified_bytes = PptxParser.apply_edit(
        content=f01_pptx_bytes,
        slide_order=2,
        shape_id=target_shape.shape_id,
        target_text="Second slide text",
        replacement_text="UPDATED_SLIDE_TEXT",
    )
    assert modified_bytes is not None

    mod_model = PptxParser.parse(modified_bytes)
    mod_slide2 = mod_model.slides[1]
    mod_shape = next(s for s in mod_slide2.all_shapes_flat if s.shape_id == target_shape.shape_id)
    assert "UPDATED_SLIDE_TEXT" in mod_shape.text_frame_content


def test_pptx_projection_to_db_records(f01_pptx_bytes):
    """to_db_records maps shapes to relational records and links parent_shape_id UUID."""
    import uuid

    model = PptxParser.parse(f01_pptx_bytes)
    build_id = uuid.uuid4()
    slide_rows, shape_rows = model.to_db_records(build_id)

    assert len(slide_rows) == model.slide_count
    assert len(shape_rows) > 0

    # Check parent_shape_id mapping for grouped child shapes
    group_row = next(r for r in shape_rows if r["shape_type"] == ShapeType.GROUP.value)
    child_rows = [r for r in shape_rows if r["parent_shape_id"] == group_row["id"]]
    assert len(child_rows) > 0, "Child shapes must reference parent group shape UUID"
