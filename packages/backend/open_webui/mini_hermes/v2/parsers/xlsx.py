"""XLSX Canonical Parser, Formula Protection Guardrail, and Mutation Engine."""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.workbook.properties import CalcProperties

from open_webui.mini_hermes.v2.canonical.spreadsheet import (
    COORD_REGEX,
    CanonicalCell,
    CanonicalMergedRange,
    CanonicalSheet,
    CanonicalSpreadsheetModel,
)
from open_webui.mini_hermes.v2.models.enums import CachedValueState, CellDataType, SheetVisibility


class XlsxFormatError(Exception):
    """Base error for XLSX operations."""

    def __init__(self, message: str, code: str = "xlsx_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class FormulaProtectionViolationError(XlsxFormatError):
    """Raised when an uncoordinated edit attempts to overwrite or clear a formula."""

    def __init__(self, coordinate: str, formula: str, attempted_value: Any):
        super().__init__(
            f"Formula Protection Guardrail: Cell {coordinate} contains formula '{formula}'. "
            f"Overwriting or clearing formulas with scalar values is blocked.",
            code="formula_protection_violation",
            status_code=422,
        )
        self.coordinate = coordinate
        self.formula = formula
        self.attempted_value = attempted_value


class MergedNonAnchorCellError(XlsxFormatError):
    """Raised when attempting to edit a non-anchor cell within a merged range."""

    def __init__(self, coordinate: str, anchor: str, range_coord: str):
        super().__init__(
            f"Cell {coordinate} is a non-anchor cell in merged range {range_coord}. "
            f"Only anchor cell {anchor} may be edited.",
            code="merged_non_anchor_cell",
            status_code=422,
        )
        self.coordinate = coordinate
        self.anchor = anchor
        self.range_coord = range_coord


class ProtectedSheetError(XlsxFormatError):
    def __init__(self, sheet_name: str):
        super().__init__(
            f"Worksheet '{sheet_name}' is protected against modifications.",
            code="sheet_protected",
            status_code=403,
        )


class XlsxParser:
    """High-fidelity XLSX parser preserving formulas, cached values, and merged ranges."""

    @staticmethod
    def parse(content: bytes) -> CanonicalSpreadsheetModel:
        """Parses workbook bytes into CanonicalSpreadsheetModel using dual-pass OpenPyXL."""
        wb_formula = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
        wb_cached = openpyxl.load_workbook(io.BytesIO(content), data_only=True)

        sheets: list[CanonicalSheet] = []

        try:
            for order, sheet_name in enumerate(wb_formula.sheetnames, start=1):
                ws_f = wb_formula[sheet_name]
                ws_c = wb_cached[sheet_name]

                # 1. Sheet visibility
                state = getattr(ws_f, "sheet_state", "visible")
                visibility = SheetVisibility.VISIBLE
                if state == "hidden":
                    visibility = SheetVisibility.HIDDEN
                elif state == "veryHidden":
                    visibility = SheetVisibility.VERY_HIDDEN

                # 2. Merged cell ranges
                merged_ranges: list[CanonicalMergedRange] = []
                for m_range in ws_f.merged_cells.ranges:
                    min_col, min_row, max_col, max_row = m_range.bounds
                    anchor_coord = f"{get_column_letter(min_col)}{min_row}"
                    merged_ranges.append(
                        CanonicalMergedRange(
                            range_coordinate=str(m_range),
                            top_row=min_row,
                            left_col=min_col,
                            bottom_row=max_row,
                            right_col=max_col,
                            anchor_coordinate=anchor_coord,
                        )
                    )

                # 3. Cells extraction
                cells: dict[str, CanonicalCell] = {}
                max_r = ws_f.max_row or 1
                max_c = ws_f.max_column or 1

                for row_idx in range(1, max_r + 1):
                    for col_idx in range(1, max_c + 1):
                        cell_f = ws_f.cell(row=row_idx, column=col_idx)
                        cell_c = ws_c.cell(row=row_idx, column=col_idx)
                        coord = cell_f.coordinate

                        raw_f = cell_f.value
                        raw_c = cell_c.value

                        if raw_f is None and raw_c is None:
                            continue  # Sparse storage: omit unformatted empty cells

                        is_formula = cell_f.data_type == "f" or (isinstance(raw_f, str) and raw_f.startswith("="))

                        if is_formula:
                            data_type = CellDataType.FORMULA
                            formula_expr = str(raw_f)
                            cached_val = raw_c
                            cached_state = (
                                CachedValueState.PRESENT if cached_val is not None else CachedValueState.MISSING
                            )
                        else:
                            formula_expr = None
                            cached_val = raw_c
                            cached_state = (
                                CachedValueState.PRESENT if cached_val is not None else CachedValueState.MISSING
                            )
                            data_type = _classify_data_type(cell_f)

                        # Check merged range relationship
                        is_merged = False
                        is_anchor = False
                        merge_parent = None
                        for mr in merged_ranges:
                            if mr.contains(row_idx, col_idx):
                                is_merged = True
                                if mr.is_anchor(row_idx, col_idx):
                                    is_anchor = True
                                else:
                                    merge_parent = mr.anchor_coordinate
                                break

                        cells[coord] = CanonicalCell(
                            coordinate=coord,
                            row_idx=row_idx,
                            col_idx=col_idx,
                            data_type=data_type,
                            raw_value=raw_f,
                            formula_expression=formula_expr,
                            cached_value=cached_val,
                            cached_value_state=cached_state,
                            number_format=cell_f.number_format,
                            is_merged=is_merged,
                            is_merge_anchor=is_anchor,
                            merge_parent=merge_parent,
                        )

                sheets.append(
                    CanonicalSheet(
                        name=sheet_name,
                        order=order,
                        visibility=visibility,
                        max_row=max_r,
                        max_col=max_c,
                        dimensions=ws_f.dimensions,
                        cells=cells,
                        merged_ranges=merged_ranges,
                    )
                )

            # Calculation properties
            full_calc = True
            if wb_formula.calculation is not None:
                full_calc = bool(wb_formula.calculation.fullCalcOnLoad)

            return CanonicalSpreadsheetModel(
                sheets=sheets,
                full_calc_on_load=full_calc,
            )

        finally:
            wb_formula.close()
            wb_cached.close()

    @staticmethod
    def apply_edit(
        content: bytes,
        sheet_name: str,
        coordinate: str,
        operation: str,
        value: Any = None,
    ) -> bytes:
        """Applies an edit operation with Formula Protection Guardrail & fullCalcOnLoad enforcement."""
        if not COORD_REGEX.match(coordinate):
            raise XlsxFormatError(f"Invalid cell coordinate format: '{coordinate}'", status_code=422)

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
        try:
            if sheet_name not in wb.sheetnames:
                raise XlsxFormatError(f"Sheet '{sheet_name}' not found in workbook.", status_code=404)

            ws = wb[sheet_name]

            # 1. Sheet Protection Guardrail
            if ws.protection and ws.protection.sheet:
                raise ProtectedSheetError(sheet_name)

            # 2. Merged Non-Anchor Guardrail
            min_col, min_row, _, _ = range_boundaries(coordinate)
            for mr in ws.merged_cells.ranges:
                if (mr.min_row <= min_row <= mr.max_row) and (mr.min_col <= min_col <= mr.max_col):
                    anchor_coord = f"{get_column_letter(mr.min_col)}{mr.min_row}"
                    if coordinate != anchor_coord:
                        raise MergedNonAnchorCellError(coordinate, anchor_coord, str(mr))

            target_cell = ws[coordinate]
            existing_val = target_cell.value
            is_existing_formula = target_cell.data_type == "f" or (
                isinstance(existing_val, str) and existing_val.startswith("=")
            )

            # 3. Formula Protection Guardrail
            if operation in ("xlsx.clear_cell_value", "clear_cell_value"):
                if is_existing_formula:
                    raise FormulaProtectionViolationError(coordinate, str(existing_val), None)
                target_cell.value = None

            elif operation in ("xlsx.set_cell_value", "set_cell_value"):
                is_new_formula = isinstance(value, str) and value.startswith("=")
                if is_existing_formula and not is_new_formula:
                    raise FormulaProtectionViolationError(coordinate, str(existing_val), value)

                # Scalar type validation
                if not isinstance(value, (str, int, float, bool, datetime, date, type(None))):
                    raise XlsxFormatError(
                        f"Unsupported value type '{type(value).__name__}' for cell {coordinate}.",
                        status_code=422,
                    )
                target_cell.value = value

            else:
                raise XlsxFormatError(f"Unsupported XLSX operation: '{operation}'", status_code=422)

            # 4. Calculation Guardrail: Force full calculation on load
            if wb.calculation is None:
                wb.calculation = CalcProperties(fullCalcOnLoad=True)
            else:
                wb.calculation.fullCalcOnLoad = True

            out = io.BytesIO()
            wb.save(out)
            return out.getvalue()
        finally:
            wb.close()


def _classify_data_type(cell: Any) -> CellDataType:
    if cell.value is None:
        return CellDataType.EMPTY
    if cell.data_type == "n":
        return CellDataType.NUMBER
    if cell.data_type == "b":
        return CellDataType.BOOLEAN
    if cell.data_type == "d":
        return CellDataType.DATE
    if cell.data_type == "e":
        return CellDataType.ERROR
    return CellDataType.STRING
