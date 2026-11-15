"""Domain 2: Format Canonical Tables (7 tables).

Tables:
11. mh_workbook_sheets
12. mh_workbook_cells
13. mh_merged_ranges
14. mh_document_sections
15. mh_document_paragraphs
16. mh_presentation_slides
17. mh_presentation_shapes
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from open_webui.mini_hermes.v2.models.base import JSONType, V2Base, utcnow


class MhWorkbookSheet(V2Base):
    """Preserves workbook sheet order, visibility, and dimensions."""

    __tablename__ = "mh_workbook_sheets"
    __table_args__ = (
        UniqueConstraint("index_build_id", "sheet_order", name="uq_mh_wb_sheets_build_order"),
        UniqueConstraint("index_build_id", "sheet_name", name="uq_mh_wb_sheets_build_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_wb_sheets_build_id"),
        nullable=False,
    )
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_order: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="visible")
    max_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhWorkbookCell(V2Base):
    """Canonical spreadsheet cell separating raw values, formulas, and cached states."""

    __tablename__ = "mh_workbook_cells"
    __table_args__ = (
        UniqueConstraint("sheet_id", "row_idx", "col_idx", name="uq_mh_wb_cells_sheet_row_col"),
        Index("ix_mh_wb_cells_sheet_coord", "sheet_id", "cell_coordinate"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_wb_cells_build_id"),
        nullable=False,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_workbook_sheets.id", ondelete="CASCADE", name="fk_mh_wb_cells_sheet_id"),
        nullable=False,
    )
    row_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    col_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_coordinate: Mapped[str] = mapped_column(String(16), nullable=False)
    data_type: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_value_state: Mapped[str] = mapped_column(String(16), nullable=False, default="MISSING")
    number_format: Mapped[str | None] = mapped_column(String(128), nullable=True)


class MhMergedRange(V2Base):
    """Preserves merged cell boundaries and anchor coordinate references."""

    __tablename__ = "mh_merged_ranges"
    __table_args__ = (
        Index("ix_mh_merged_ranges_sheet_coord", "sheet_id", "range_coordinate"),
        Index("ix_mh_merged_ranges_anchor", "anchor_cell_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_workbook_sheets.id", ondelete="CASCADE", name="fk_mh_merged_ranges_sheet_id"),
        nullable=False,
    )
    anchor_cell_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_workbook_cells.id", ondelete="RESTRICT", name="fk_mh_merged_ranges_anchor_id"),
        nullable=False,
    )
    range_coordinate: Mapped[str] = mapped_column(String(32), nullable=False)
    top_row: Mapped[int] = mapped_column(Integer, nullable=False)
    left_col: Mapped[int] = mapped_column(Integer, nullable=False)
    bottom_row: Mapped[int] = mapped_column(Integer, nullable=False)
    right_col: Mapped[int] = mapped_column(Integer, nullable=False)


class MhDocumentSection(V2Base):
    """Preserves document sections, headers, and footers."""

    __tablename__ = "mh_document_sections"
    __table_args__ = (
        UniqueConstraint("index_build_id", "section_order", name="uq_mh_doc_sections_build_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_doc_sections_build_id"),
        nullable=False,
    )
    section_order: Mapped[int] = mapped_column(Integer, nullable=False)
    header_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class MhDocumentParagraph(V2Base):
    """Stores structured paragraph runs, style properties, and XML run boundaries."""

    __tablename__ = "mh_document_paragraphs"
    __table_args__ = (
        UniqueConstraint("section_id", "paragraph_order", name="uq_mh_doc_paras_sec_order"),
        Index("ix_mh_doc_paras_sec", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_sections.id", ondelete="CASCADE", name="fk_mh_doc_paras_sec_id"),
        nullable=False,
    )
    paragraph_order: Mapped[int] = mapped_column(Integer, nullable=False)
    style_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    runs_json: Mapped[list | dict | None] = mapped_column(JSONType, nullable=True)
    hyperlinks_json: Mapped[list | dict | None] = mapped_column(JSONType, nullable=True)


class MhPresentationSlide(V2Base):
    """Preserves slide ordering, layouts, titles, and speaker notes."""

    __tablename__ = "mh_presentation_slides"
    __table_args__ = (
        UniqueConstraint("index_build_id", "slide_order", name="uq_mh_pres_slides_build_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_pres_slides_build_id"),
        nullable=False,
    )
    slide_order: Mapped[int] = mapped_column(Integer, nullable=False)
    slide_number: Mapped[int] = mapped_column(Integer, nullable=False)
    layout_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    speaker_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MhPresentationShape(V2Base):
    """Stores shapes, text frames, and group hierarchies."""

    __tablename__ = "mh_presentation_shapes"
    __table_args__ = (
        Index("ix_mh_pres_shapes_slide_type", "slide_id", "shape_type"),
        Index("ix_mh_pres_shapes_parent", "parent_shape_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slide_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_presentation_slides.id", ondelete="CASCADE", name="fk_mh_pres_shapes_slide_id"),
        nullable=False,
    )
    parent_shape_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_presentation_shapes.id", ondelete="CASCADE", name="fk_mh_pres_shapes_parent_id"),
        nullable=True,
    )
    shape_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shape_type: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    text_frame: Mapped[str | None] = mapped_column(Text, nullable=True)
