"""Canonical spreadsheet models for Mini-Hermes V2."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from open_webui.mini_hermes.v2.models.enums import CachedValueState, CellDataType, SheetVisibility

COORD_REGEX = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


@dataclass(slots=True)
class CanonicalMergedRange:
    """Represents a merged cell range within a sheet."""

    range_coordinate: str  # e.g., "A5:B5"
    top_row: int  # 1-indexed
    left_col: int  # 1-indexed
    bottom_row: int  # 1-indexed
    right_col: int  # 1-indexed
    anchor_coordinate: str  # e.g., "A5"

    def contains(self, row: int, col: int) -> bool:
        return (self.top_row <= row <= self.bottom_row) and (self.left_col <= col <= self.right_col)

    def is_anchor(self, row: int, col: int) -> bool:
        return self.top_row == row and self.left_col == col

    def is_non_anchor(self, row: int, col: int) -> bool:
        return self.contains(row, col) and not self.is_anchor(row, col)


@dataclass(slots=True)
class CanonicalCell:
    """Canonical representation of an individual spreadsheet cell."""

    coordinate: str  # e.g., "A1", "B2"
    row_idx: int  # 1-indexed
    col_idx: int  # 1-indexed
    data_type: CellDataType  # STRING, NUMBER, BOOLEAN, DATE, FORMULA, EMPTY, ERROR
    raw_value: Any | None  # Scalar value or formula string
    formula_expression: str | None  # e.g., "=A2*2" or None
    cached_value: Any | None  # Evaluated value from <v> tag
    cached_value_state: CachedValueState  # PRESENT, MISSING, DIRTY
    number_format: str | None = None
    is_merged: bool = False
    is_merge_anchor: bool = False
    merge_parent: str | None = None  # Coordinate of anchor cell if non-anchor

    @property
    def has_formula(self) -> bool:
        return self.formula_expression is not None or self.data_type == CellDataType.FORMULA

    def to_display_string(self) -> str:
        """Renders cell for textual/table representation."""
        if self.has_formula:
            cached_repr = (
                f" [cached={self.cached_value}]"
                if self.cached_value_state == CachedValueState.PRESENT
                else " [cached=MISSING]"
            )
            return f"{self.coordinate}={self.formula_expression}{cached_repr}"
        return f"{self.coordinate}={self.raw_value}"


@dataclass
class CanonicalSheet:
    """Canonical representation of a single worksheet."""

    name: str
    order: int  # 1-indexed sheet sequence
    visibility: SheetVisibility  # VISIBLE, HIDDEN, VERY_HIDDEN
    max_row: int
    max_col: int
    dimensions: str | None
    cells: dict[str, CanonicalCell] = field(default_factory=dict)
    merged_ranges: list[CanonicalMergedRange] = field(default_factory=list)

    def get_cell(self, coordinate: str) -> CanonicalCell | None:
        return self.cells.get(coordinate)

    def get_cell_by_indices(self, row: int, col: int) -> CanonicalCell | None:
        from openpyxl.utils import get_column_letter

        coord = f"{get_column_letter(col)}{row}"
        return self.cells.get(coord)

    def get_merged_range(self, coordinate: str) -> CanonicalMergedRange | None:
        m = COORD_REGEX.match(coordinate)
        if not m:
            return None
        from openpyxl.utils import column_index_from_string

        col = column_index_from_string(m.group(1))
        row = int(m.group(2))
        for r in self.merged_ranges:
            if r.contains(row, col):
                return r
        return None

    def is_merged_non_anchor(self, coordinate: str) -> tuple[bool, str | None]:
        r = self.get_merged_range(coordinate)
        if r and r.anchor_coordinate != coordinate:
            return True, r.anchor_coordinate
        return False, None


@dataclass
class CanonicalSpreadsheetModel:
    """Top-level canonical model representing an entire workbook."""

    sheets: list[CanonicalSheet] = field(default_factory=list)
    full_calc_on_load: bool = True
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def sheet_count(self) -> int:
        return len(self.sheets)

    def get_sheet(self, name: str) -> CanonicalSheet | None:
        for s in self.sheets:
            if s.name == name:
                return s
        return None

    def get_sheet_by_order(self, order: int) -> CanonicalSheet | None:
        for s in self.sheets:
            if s.order == order:
                return s
        return None

    def to_db_records(
        self, index_build_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Projects canonical spreadsheet into Domain 2 table dictionaries."""
        sheet_rows: list[dict[str, Any]] = []
        cell_rows: list[dict[str, Any]] = []
        merge_rows: list[dict[str, Any]] = []

        for sheet in self.sheets:
            sheet_id = uuid.uuid4()
            sheet_rows.append(
                {
                    "id": sheet_id,
                    "index_build_id": index_build_id,
                    "sheet_name": sheet.name,
                    "sheet_order": sheet.order,
                    "visibility": (
                        sheet.visibility.value if hasattr(sheet.visibility, "value") else str(sheet.visibility)
                    ),
                    "max_row": sheet.max_row,
                    "max_col": sheet.max_col,
                }
            )

            # Map cell coordinates to UUID for merge anchor references
            coord_to_id: dict[str, uuid.UUID] = {}
            for coord, cell in sheet.cells.items():
                cell_id = uuid.uuid4()
                coord_to_id[coord] = cell_id
                cell_rows.append(
                    {
                        "id": cell_id,
                        "index_build_id": index_build_id,
                        "sheet_id": sheet_id,
                        "row_idx": cell.row_idx,
                        "col_idx": cell.col_idx,
                        "cell_coordinate": cell.coordinate,
                        "data_type": cell.data_type.value if hasattr(cell.data_type, "value") else str(cell.data_type),
                        "raw_value": str(cell.raw_value) if cell.raw_value is not None else None,
                        "formula_expression": cell.formula_expression,
                        "cached_value": str(cell.cached_value) if cell.cached_value is not None else None,
                        "cached_value_state": (
                            cell.cached_value_state.value
                            if hasattr(cell.cached_value_state, "value")
                            else str(cell.cached_value_state)
                        ),
                        "number_format": cell.number_format,
                    }
                )

            for m in sheet.merged_ranges:
                anchor_id = coord_to_id.get(m.anchor_coordinate)
                if anchor_id:
                    merge_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "sheet_id": sheet_id,
                            "anchor_cell_id": anchor_id,
                            "range_coordinate": m.range_coordinate,
                            "top_row": m.top_row,
                            "left_col": m.left_col,
                            "bottom_row": m.bottom_row,
                            "right_col": m.right_col,
                        }
                    )

        return sheet_rows, cell_rows, merge_rows
