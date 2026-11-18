"""PPTX Canonical Parser with Recursive Group Shape Traversal."""

from __future__ import annotations

import io
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from weai.persistence.v2.canonical.presentation import (
    CanonicalPresentationModel,
    CanonicalShape,
    CanonicalSlide,
)
from weai.persistence.v2.models.enums import ShapeType


class PptxFormatError(Exception):
    def __init__(self, message: str, code: str = "pptx_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class PptxParser:
    """High-fidelity PPTX parser with complete recursive grouped shape extraction."""

    @staticmethod
    def parse(content: bytes) -> CanonicalPresentationModel:
        prs = Presentation(io.BytesIO(content))
        slides: list[CanonicalSlide] = []

        for s_idx, slide in enumerate(prs.slides, start=1):
            # 1. Slide metadata
            title = None
            if slide.shapes.title and slide.shapes.title.has_text_frame:
                title = slide.shapes.title.text

            layout_name = slide.slide_layout.name if slide.slide_layout else None

            # 2. Speaker notes
            speaker_notes = None
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                speaker_notes = slide.notes_slide.notes_text_frame.text

            # 3. Recursive shapes extraction
            top_shapes = _extract_shapes_recursive(slide.shapes, parent_id=None)

            slides.append(
                CanonicalSlide(
                    slide_order=s_idx,
                    slide_number=s_idx,
                    slide_id=slide.slide_id,
                    layout_name=layout_name,
                    title=title,
                    speaker_notes=speaker_notes,
                    shapes=top_shapes,
                )
            )

        return CanonicalPresentationModel(
            slide_width=prs.slide_width,
            slide_height=prs.slide_height,
            slides=slides,
        )

    @staticmethod
    def apply_edit(
        content: bytes,
        slide_order: int,
        shape_id: int,
        target_text: str,
        replacement_text: str,
    ) -> bytes:
        """Replaces exact text in a target shape within a slide."""
        prs = Presentation(io.BytesIO(content))
        if slide_order < 1 or slide_order > len(prs.slides):
            raise PptxFormatError(f"Slide order {slide_order} out of bounds.", status_code=404)

        slide = prs.slides[slide_order - 1]

        # Locate shape by ID (searching recursively)
        target_shape = None

        def _find_shape(shape_coll: Any) -> None:
            nonlocal target_shape
            for s in shape_coll:
                if s.shape_id == shape_id:
                    target_shape = s
                    return
                if s.shape_type == MSO_SHAPE_TYPE.GROUP:
                    _find_shape(s.shapes)

        _find_shape(slide.shapes)
        if target_shape is None:
            raise PptxFormatError(f"Shape ID {shape_id} not found on slide {slide_order}.", status_code=404)

        if target_shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            raise PptxFormatError(
                f"Shape {shape_id} is a GroupShape container. Direct text edit is unsupported; target child shape.",
                code="pptx_grouped_shape_edit_blocked",
                status_code=422,
            )

        if not target_shape.has_text_frame:
            raise PptxFormatError(f"Shape {shape_id} does not contain a text frame.", status_code=422)

        tf = target_shape.text_frame
        if target_text not in tf.text:
            raise PptxFormatError(f"Target text '{target_text}' not found in shape {shape_id}.", status_code=404)

        # Exact replacement preserving paragraph structure
        for p in tf.paragraphs:
            if target_text in p.text:
                p.text = p.text.replace(target_text, replacement_text)
                break

        out = io.BytesIO()
        prs.save(out)
        return out.getvalue()


def _extract_shapes_recursive(shape_collection: Any, parent_id: str | None = None) -> list[CanonicalShape]:
    """Recursively traverses shapes and group containers at arbitrary nesting depths."""
    shapes: list[CanonicalShape] = []
    for s in shape_collection:
        shape_type = _map_shape_type(s.shape_type)
        is_group = s.shape_type == MSO_SHAPE_TYPE.GROUP

        geometry = {
            "left": s.left,
            "top": s.top,
            "width": s.width,
            "height": s.height,
            "rotation": getattr(s, "rotation", 0),
        }

        tf_content = s.text_frame.text if s.has_text_frame else None
        structured_p = [p.text for p in s.text_frame.paragraphs] if s.has_text_frame else []

        c_shape = CanonicalShape(
            shape_id=s.shape_id,
            shape_name=s.name,
            shape_type=shape_type,
            parent_shape_id=parent_id,
            geometry=geometry,
            text_frame_content=tf_content,
            structured_paragraphs=structured_p,
            is_group=is_group,
            children=[],
        )

        if is_group:
            c_shape.children = _extract_shapes_recursive(s.shapes, parent_id=str(s.shape_id))

        shapes.append(c_shape)
    return shapes


def _map_shape_type(mso_type: MSO_SHAPE_TYPE) -> ShapeType:
    if mso_type == MSO_SHAPE_TYPE.GROUP:
        return ShapeType.GROUP
    if mso_type == MSO_SHAPE_TYPE.TEXT_BOX:
        return ShapeType.TEXT_BOX
    if mso_type == MSO_SHAPE_TYPE.TABLE:
        return ShapeType.TABLE
    if mso_type == MSO_SHAPE_TYPE.PICTURE:
        return ShapeType.PICTURE
    if mso_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
        return ShapeType.AUTO_SHAPE
    return ShapeType.GRAPHIC_FRAME
