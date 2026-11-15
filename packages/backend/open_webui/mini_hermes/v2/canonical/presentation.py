"""Canonical presentation models for Mini-Hermes V2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from open_webui.mini_hermes.v2.models.enums import ShapeType


@dataclass
class CanonicalShape:
    """Represents a shape, graphic frame, or grouped container in a slide."""

    shape_id: int | str  # PPTX shape ID
    shape_name: str | None
    shape_type: ShapeType
    parent_shape_id: str | None = None  # None for top-level shapes
    geometry: dict[str, Any] = field(default_factory=dict)
    text_frame_content: str | None = None
    structured_paragraphs: list[str] = field(default_factory=list)
    is_group: bool = False
    children: list[CanonicalShape] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.text_frame_content and self.text_frame_content.strip())


@dataclass
class CanonicalSlide:
    """Represents an individual presentation slide."""

    slide_order: int  # 1-indexed presentation sequence
    slide_number: int  # Presentation slide number
    slide_id: int  # Internal PPTX slide ID
    layout_name: str | None = None
    title: str | None = None
    speaker_notes: str | None = None
    shapes: list[CanonicalShape] = field(default_factory=list)

    @property
    def all_shapes_flat(self) -> list[CanonicalShape]:
        """Returns a flattened list of all shapes including nested group children."""
        flat: list[CanonicalShape] = []

        def _flatten(s_list: list[CanonicalShape]) -> None:
            for s in s_list:
                flat.append(s)
                if s.is_group:
                    _flatten(s.children)

        _flatten(self.shapes)
        return flat


@dataclass
class CanonicalPresentationModel:
    """Top-level presentation model representing an entire PowerPoint file."""

    slide_width: int
    slide_height: int
    slides: list[CanonicalSlide] = field(default_factory=list)

    @property
    def slide_count(self) -> int:
        return len(self.slides)

    def to_db_records(
        self, index_build_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Projects canonical presentation into Domain 2 table dictionaries."""
        slide_rows: list[dict[str, Any]] = []
        shape_rows: list[dict[str, Any]] = []

        for slide in self.slides:
            slide_db_id = uuid.uuid4()
            slide_rows.append(
                {
                    "id": slide_db_id,
                    "index_build_id": index_build_id,
                    "slide_order": slide.slide_order,
                    "slide_number": slide.slide_number,
                    "layout_name": slide.layout_name,
                    "title": slide.title,
                    "speaker_notes": slide.speaker_notes,
                }
            )

            shape_id_to_uuid: dict[str, uuid.UUID] = {}

            # First pass: allocate UUIDs for all shapes
            flat_shapes = slide.all_shapes_flat
            for s in flat_shapes:
                shape_id_to_uuid[str(s.shape_id)] = uuid.uuid4()

            # Second pass: generate rows with parent UUID foreign keys
            for s in flat_shapes:
                parent_uuid = shape_id_to_uuid.get(str(s.parent_shape_id)) if s.parent_shape_id else None
                shape_type_val = s.shape_type.value if hasattr(s.shape_type, "value") else str(s.shape_type)
                shape_rows.append(
                    {
                        "id": shape_id_to_uuid[str(s.shape_id)],
                        "slide_id": slide_db_id,
                        "parent_shape_id": parent_uuid,
                        "shape_name": s.shape_name,
                        "shape_type": shape_type_val,
                        "geometry_json": s.geometry,
                        "text_frame": s.text_frame_content,
                    }
                )

        return slide_rows, shape_rows
