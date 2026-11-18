"""Canonical document models for Agent Runtime V2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CanonicalRun:
    """Represents an inline text run with character formatting properties."""

    text: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    color_rgb: str | None = None
    highlight_color: str | None = None
    is_hyperlink: bool = False
    hyperlink_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "font_name": self.font_name,
            "font_size_pt": self.font_size_pt,
            "color_rgb": self.color_rgb,
            "highlight_color": self.highlight_color,
            "is_hyperlink": self.is_hyperlink,
            "hyperlink_target": self.hyperlink_target,
        }


@dataclass
class CanonicalParagraph:
    """Represents a structured paragraph containing styled runs and hyperlinks."""

    paragraph_order: int  # 1-indexed document sequence
    style_id: str | None
    raw_text: str
    runs: list[CanonicalRun] = field(default_factory=list)
    hyperlinks: list[dict[str, Any]] = field(default_factory=list)
    is_in_table: bool = False
    table_index: int | None = None


@dataclass
class CanonicalTable:
    """Represents a structured Word table."""

    table_order: int
    num_rows: int
    num_cols: int
    cells_matrix: list[list[str]] = field(default_factory=list)


@dataclass
class CanonicalSection:
    """Represents a document section with header/footer text and page geometry."""

    section_order: int  # 1-indexed
    header_text: str | None = None
    footer_text: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    paragraphs: list[CanonicalParagraph] = field(default_factory=list)
    tables: list[CanonicalTable] = field(default_factory=list)


@dataclass
class CanonicalDocumentModel:
    """Top-level document model representing a complete DOCX file."""

    sections: list[CanonicalSection] = field(default_factory=list)

    @property
    def all_paragraphs(self) -> list[CanonicalParagraph]:
        paras: list[CanonicalParagraph] = []
        for sec in self.sections:
            paras.extend(sec.paragraphs)
        return paras

    def to_db_records(
        self, index_build_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Projects canonical document into Domain 2 table dictionaries."""
        section_rows: list[dict[str, Any]] = []
        paragraph_rows: list[dict[str, Any]] = []

        for sec in self.sections:
            sec_id = uuid.uuid4()
            section_rows.append(
                {
                    "id": sec_id,
                    "index_build_id": index_build_id,
                    "section_order": sec.section_order,
                    "header_text": sec.header_text,
                    "footer_text": sec.footer_text,
                    "properties_json": sec.properties,
                }
            )

            for p in sec.paragraphs:
                paragraph_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "section_id": sec_id,
                        "paragraph_order": p.paragraph_order,
                        "style_id": p.style_id,
                        "raw_text": p.raw_text,
                        "runs_json": [r.to_dict() for r in p.runs],
                        "hyperlinks_json": p.hyperlinks,
                    }
                )

        return section_rows, paragraph_rows
