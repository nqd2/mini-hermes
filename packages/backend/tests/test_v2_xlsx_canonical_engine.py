"""Tests for XLSX Canonical Engine, Dual-Pass OpenPyXL, and Formula Guardrail (M2.2)."""

import io
from pathlib import Path

import openpyxl
import pytest

from weai.persistence.v2.canonical.spreadsheet import (
    CanonicalMergedRange,
    CanonicalSpreadsheetModel,
)
from weai.persistence.v2.models.enums import CachedValueState, CellDataType, SheetVisibility
from weai.persistence.v2.parsers.xlsx import (
    FormulaProtectionViolationError,
    MergedNonAnchorCellError,
    ProtectedSheetError,
    XlsxFormatError,
    XlsxParser,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


@pytest.fixture
def f05_synthetic_bytes() -> bytes:
    return (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()


@pytest.fixture
def f01_f03_hidden_missing_cache_bytes() -> bytes:
    return (FIXTURES_DIR / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()


def test_xlsx_sheet_order_visibility_and_dimensions(f01_f03_hidden_missing_cache_bytes):
    """Verifies dual-pass parser extracts all sheets, ordering, and visibility correctly."""
    model = XlsxParser.parse(f01_f03_hidden_missing_cache_bytes)
    assert isinstance(model, CanonicalSpreadsheetModel)
    assert model.sheet_count >= 2

    sheet1 = model.get_sheet_by_order(1)
    sheet2 = model.get_sheet_by_order(2)
    assert sheet1 is not None
    assert sheet2 is not None

    assert sheet1.visibility == SheetVisibility.VISIBLE
    assert sheet2.visibility == SheetVisibility.HIDDEN


def test_xlsx_formula_preservation_and_missing_cached_value(f01_f03_hidden_missing_cache_bytes):
    """Verifies formula string is preserved and missing cached values are accurately classified."""
    model = XlsxParser.parse(f01_f03_hidden_missing_cache_bytes)
    sheet1 = model.get_sheet_by_order(1)

    formula_cell = sheet1.get_cell("B2")
    assert formula_cell is not None
    assert formula_cell.has_formula
    assert formula_cell.formula_expression == "=A2*2"
    assert formula_cell.cached_value is None
    assert formula_cell.cached_value_state == CachedValueState.MISSING
    assert formula_cell.data_type == CellDataType.FORMULA


def test_xlsx_present_cached_value(f05_synthetic_bytes):
    """Verifies formula cells with evaluated caches are identified as PRESENT."""
    model = XlsxParser.parse(f05_synthetic_bytes)
    found_present = False
    for sheet in model.sheets:
        for coord, cell in sheet.cells.items():
            if cell.has_formula and cell.cached_value_state == CachedValueState.PRESENT:
                found_present = True
                assert cell.cached_value is not None
                break
        if found_present:
            break
    assert found_present, "Expected at least one formula cell with PRESENT cached value in f05_synthetic"


def test_xlsx_merged_ranges_preservation(f05_synthetic_bytes):
    """Verifies merged ranges are captured and anchor vs non-anchor cells are identified."""
    model = XlsxParser.parse(f05_synthetic_bytes)
    merged_ranges_total = sum(len(s.merged_ranges) for s in model.sheets)
    assert merged_ranges_total > 0

    sheet = next(s for s in model.sheets if len(s.merged_ranges) > 0)
    mr = sheet.merged_ranges[0]
    assert isinstance(mr, CanonicalMergedRange)
    assert mr.anchor_coordinate is not None

    # Check anchor cell
    anchor_cell = sheet.get_cell(mr.anchor_coordinate)
    if anchor_cell:
        assert anchor_cell.is_merged
        assert anchor_cell.is_merge_anchor

    is_non_anchor, parent = sheet.is_merged_non_anchor(mr.anchor_coordinate)
    assert not is_non_anchor


def test_xlsx_formula_protection_guardrail(f01_f03_hidden_missing_cache_bytes):
    """Formula Protection Guardrail blocks uncoordinated overwrite with scalar values."""
    sheet_name = "Visible"

    # 1. Overwrite formula with scalar value -> BLOCKED
    with pytest.raises(FormulaProtectionViolationError) as exc_info:
        XlsxParser.apply_edit(
            content=f01_f03_hidden_missing_cache_bytes,
            sheet_name=sheet_name,
            coordinate="B2",
            operation="xlsx.set_cell_value",
            value=100,
        )
    assert exc_info.value.status_code == 422
    assert "Formula Protection Guardrail" in str(exc_info.value)

    # 2. Clear cell containing formula -> BLOCKED
    with pytest.raises(FormulaProtectionViolationError):
        XlsxParser.apply_edit(
            content=f01_f03_hidden_missing_cache_bytes,
            sheet_name=sheet_name,
            coordinate="B2",
            operation="xlsx.clear_cell_value",
        )

    # 3. Overwrite formula with a new formula expression -> ALLOWED
    updated_bytes = XlsxParser.apply_edit(
        content=f01_f03_hidden_missing_cache_bytes,
        sheet_name=sheet_name,
        coordinate="B2",
        operation="xlsx.set_cell_value",
        value="=A2*5",
    )
    assert updated_bytes is not None
    updated_model = XlsxParser.parse(updated_bytes)
    updated_cell = updated_model.get_sheet(sheet_name).get_cell("B2")
    assert updated_cell.formula_expression == "=A2*5"


def test_xlsx_merged_non_anchor_edit_rejected(f05_synthetic_bytes):
    """Direct edits to non-anchor cells in merged ranges must be rejected."""
    model = XlsxParser.parse(f05_synthetic_bytes)
    sheet = next(s for s in model.sheets if len(s.merged_ranges) > 0)
    mr = sheet.merged_ranges[0]

    # Non-anchor coordinate
    from openpyxl.utils import get_column_letter

    non_anchor_coord = f"{get_column_letter(mr.right_col)}{mr.bottom_row}"
    if non_anchor_coord == mr.anchor_coordinate:
        pytest.skip("Single cell merge range in test fixture")

    with pytest.raises(MergedNonAnchorCellError) as exc_info:
        XlsxParser.apply_edit(
            content=f05_synthetic_bytes,
            sheet_name=sheet.name,
            coordinate=non_anchor_coord,
            operation="xlsx.set_cell_value",
            value="Modified",
        )
    assert exc_info.value.status_code == 422
    assert mr.anchor_coordinate in str(exc_info.value)


def test_xlsx_full_calc_on_load_enforced_on_save(f01_f03_hidden_missing_cache_bytes):
    """On every modification/save, fullCalcOnLoad must be explicitly set to True."""
    modified_bytes = XlsxParser.apply_edit(
        content=f01_f03_hidden_missing_cache_bytes,
        sheet_name="Visible",
        coordinate="A2",
        operation="xlsx.set_cell_value",
        value=50,
    )
    wb = openpyxl.load_workbook(io.BytesIO(modified_bytes), data_only=False)
    try:
        assert wb.calculation is not None
        assert wb.calculation.fullCalcOnLoad is True
    finally:
        wb.close()


def test_xlsx_projection_to_db_records(f05_synthetic_bytes):
    """to_db_records projects model into Domain 2 table rows with foreign key mappings."""
    import uuid

    model = XlsxParser.parse(f05_synthetic_bytes)
    build_id = uuid.uuid4()
    sheet_rows, cell_rows, merge_rows = model.to_db_records(build_id)

    assert len(sheet_rows) == model.sheet_count
    assert len(cell_rows) > 0
    assert len(merge_rows) > 0

    # Verify keys
    for s in sheet_rows:
        assert s["index_build_id"] == build_id
        assert "sheet_name" in s
        assert "sheet_order" in s

    for c in cell_rows:
        assert c["index_build_id"] == build_id
        assert "cell_coordinate" in c
        assert "data_type" in c

    for m in merge_rows:
        assert "anchor_cell_id" in m
        assert "range_coordinate" in m
