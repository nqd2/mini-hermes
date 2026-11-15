"""Empirical Adversarial Challenge Test Suite for Milestone M2.

Stress-tests:
1. XLSX Canonical Engine & Formula Guardrail:
   - Scalar overwrite, clear cell, empty string, formula deletion on formula cells (A1, B9, B2) -> HTTP 422 formula_protection_violation
   - Non-anchor cell edits in merged ranges (B5 in A5:B5, B1/C1/D1 in A1:D1) -> HTTP 422 merged_non_anchor_cell
   - Formula expression preservation <f> with data_only=False and cached value states (PRESENT in F05, MISSING in F03)
   - wb.calculation.fullCalcOnLoad = True guarantee on workbook save/export
2. DOCX OpenXML RunSpanMatcher:
   - Multi-run search & replace across 2, 3, 5 runs with heterogeneous formatting (bold, italic, font, color, underline, size)
   - Replacement text inherits opening run's exact <w:rPr> properties
   - Intermediate runs cleared of text and closing run preserves suffix formatting
   - Fail-closed guardrails: tracked changes (<w:ins>, <w:del>, <w:moveFrom>, <w:moveTo>), complex fields (<w:fldSimple>, <w:fldChar>), cross-hyperlink spans
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import docx
import openpyxl
import pytest
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor
from openpyxl.workbook.properties import CalcProperties

from open_webui.mini_hermes.v2.canonical.document import CanonicalDocumentModel
from open_webui.mini_hermes.v2.canonical.spreadsheet import (
    CanonicalMergedRange,
    CanonicalSpreadsheetModel,
)
from open_webui.mini_hermes.v2.models.enums import CachedValueState, CellDataType, SheetVisibility
from open_webui.mini_hermes.v2.parsers.docx import (
    AmbiguousMatchError,
    DocxFormatError,
    DocxParser,
    RunSpanMatcher,
    TextNotFoundError,
    UnsupportedContentError,
)
from open_webui.mini_hermes.v2.parsers.xlsx import (
    FormulaProtectionViolationError,
    MergedNonAnchorCellError,
    ProtectedSheetError,
    XlsxFormatError,
    XlsxParser,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


@pytest.fixture
def f01_docx_bytes() -> bytes:
    return (FIXTURES_DIR / "docx-f01-structured.docx").read_bytes()


@pytest.fixture
def f05_synthetic_bytes() -> bytes:
    return (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()


@pytest.fixture
def f01_f03_hidden_missing_cache_bytes() -> bytes:
    return (FIXTURES_DIR / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()


@pytest.fixture
def synthetic_formula_and_merge_workbook_bytes() -> bytes:
    """Creates a synthetic workbook containing:
    - Sheet 'Calc':
      - Cell A1 with formula '=SUM(10, 20)'
      - Cell B9 with formula '=A1*2'
      - Cell B2 with formula '=B9+5'
      - Cell C1 with scalar 100
      - Merged range A5:B5 (Anchor A5='AnchorText', Non-anchor B5)
      - Merged range C7:E9 (3x3 merge; Anchor C7='MultiMerge')
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Calc"

    # Formulas in A1, B9, B2
    ws["A1"] = "=SUM(10, 20)"
    ws["B9"] = "=A1*2"
    ws["B2"] = "=B9+5"

    # Scalar cell
    ws["C1"] = 100

    # Merged ranges
    ws.merge_cells("A5:B5")
    ws["A5"] = "AnchorText"

    ws.merge_cells("C7:E9")
    ws["C7"] = "MultiMerge"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================================
# 1. XLSX Canonical Engine & Formula Guardrail Adversarial Challenges
# ============================================================================


class TestXlsxFormulaProtectionAdversarial:
    """Adversarial stress-testing of Formula Protection Guardrail."""

    @pytest.mark.parametrize("coord", ["A1", "B9", "B2"])
    @pytest.mark.parametrize(
        "bad_value",
        [
            999,
            -42,
            0,
            3.14159,
            "scalar_string",
            "123",
            True,
            False,
            datetime(2026, 11, 14, 12, 0, 0),
            date(2026, 11, 14),
            "",  # empty string
            "   ",  # whitespace
            None,  # formula deletion via None
        ],
    )
    def test_adv_xlsx_formula_protection_scalar_overwrite_blocked(
        self, synthetic_formula_and_merge_workbook_bytes, coord: str, bad_value
    ):
        """Attempts to overwrite formula cells (A1, B9, B2) with scalar values must raise 422."""
        with pytest.raises(FormulaProtectionViolationError) as exc_info:
            XlsxParser.apply_edit(
                content=synthetic_formula_and_merge_workbook_bytes,
                sheet_name="Calc",
                coordinate=coord,
                operation="xlsx.set_cell_value",
                value=bad_value,
            )
        err = exc_info.value
        assert err.status_code == 422
        assert err.code == "formula_protection_violation"
        assert err.coordinate == coord
        assert "Formula Protection Guardrail" in str(err)

    @pytest.mark.parametrize("coord", ["A1", "B9", "B2"])
    @pytest.mark.parametrize("op", ["xlsx.clear_cell_value", "clear_cell_value"])
    def test_adv_xlsx_formula_protection_clear_cell_blocked(
        self, synthetic_formula_and_merge_workbook_bytes, coord: str, op: str
    ):
        """Attempts to clear formula cells (A1, B9, B2) via clear_cell_value must raise 422."""
        with pytest.raises(FormulaProtectionViolationError) as exc_info:
            XlsxParser.apply_edit(
                content=synthetic_formula_and_merge_workbook_bytes,
                sheet_name="Calc",
                coordinate=coord,
                operation=op,
            )
        err = exc_info.value
        assert err.status_code == 422
        assert err.code == "formula_protection_violation"
        assert err.coordinate == coord

    def test_adv_xlsx_formula_protection_f01_b2_missing_cache(self, f01_f03_hidden_missing_cache_bytes):
        """Cell B2 in fixture xlsx-f01 contains formula '=A2*2' with MISSING cache."""
        # Scalar overwrite
        with pytest.raises(FormulaProtectionViolationError) as exc1:
            XlsxParser.apply_edit(
                content=f01_f03_hidden_missing_cache_bytes,
                sheet_name="Visible",
                coordinate="B2",
                operation="xlsx.set_cell_value",
                value=42,
            )
        assert exc1.value.status_code == 422
        assert exc1.value.code == "formula_protection_violation"

        # Clear cell
        with pytest.raises(FormulaProtectionViolationError) as exc2:
            XlsxParser.apply_edit(
                content=f01_f03_hidden_missing_cache_bytes,
                sheet_name="Visible",
                coordinate="B2",
                operation="xlsx.clear_cell_value",
            )
        assert exc2.value.status_code == 422
        assert exc2.value.code == "formula_protection_violation"

    def test_adv_xlsx_formula_protection_f05_b9_present_cache(self, f05_synthetic_bytes):
        """Cell B9 in fixture xlsx-f05 contains formula '=ROW()-1' with PRESENT cache."""
        with pytest.raises(FormulaProtectionViolationError) as exc_info:
            XlsxParser.apply_edit(
                content=f05_synthetic_bytes,
                sheet_name="Meta",
                coordinate="B9",
                operation="xlsx.set_cell_value",
                value="DestroyFormula",
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "formula_protection_violation"

    def test_adv_xlsx_formula_replacement_with_new_formula_succeeds(
        self, synthetic_formula_and_merge_workbook_bytes
    ):
        """Overwriting a formula cell with a coordinated NEW formula must succeed."""
        updated = XlsxParser.apply_edit(
            content=synthetic_formula_and_merge_workbook_bytes,
            sheet_name="Calc",
            coordinate="B9",
            operation="xlsx.set_cell_value",
            value="=SUM(A1:C1)",
        )
        model = XlsxParser.parse(updated)
        cell = model.get_sheet("Calc").get_cell("B9")
        assert cell is not None
        assert cell.has_formula
        assert cell.formula_expression == "=SUM(A1:C1)"


class TestXlsxMergedRangesAdversarial:
    """Adversarial stress-testing of merged cell range guards."""

    def test_adv_xlsx_merged_non_anchor_b5_in_a5_b5_rejected(
        self, synthetic_formula_and_merge_workbook_bytes
    ):
        """Editing non-anchor cell B5 in merged range A5:B5 must raise HTTP 422."""
        with pytest.raises(MergedNonAnchorCellError) as exc_info:
            XlsxParser.apply_edit(
                content=synthetic_formula_and_merge_workbook_bytes,
                sheet_name="Calc",
                coordinate="B5",
                operation="xlsx.set_cell_value",
                value="IllegalNonAnchorEdit",
            )
        err = exc_info.value
        assert err.status_code == 422
        assert err.code == "merged_non_anchor_cell"
        assert err.coordinate == "B5"
        assert err.anchor == "A5"
        assert "A5:B5" in err.range_coord

    def test_adv_xlsx_merged_non_anchor_clear_rejected(
        self, synthetic_formula_and_merge_workbook_bytes
    ):
        """Clearing non-anchor cell B5 in merged range A5:B5 must raise HTTP 422."""
        with pytest.raises(MergedNonAnchorCellError) as exc_info:
            XlsxParser.apply_edit(
                content=synthetic_formula_and_merge_workbook_bytes,
                sheet_name="Calc",
                coordinate="B5",
                operation="xlsx.clear_cell_value",
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "merged_non_anchor_cell"

    @pytest.mark.parametrize("non_anchor", ["D7", "E7", "C8", "D8", "E8", "C9", "D9", "E9"])
    def test_adv_xlsx_multi_cell_merge_all_non_anchors_rejected(
        self, synthetic_formula_and_merge_workbook_bytes, non_anchor: str
    ):
        """All 8 non-anchor cells in 3x3 merged range C7:E9 must raise HTTP 422."""
        with pytest.raises(MergedNonAnchorCellError) as exc_info:
            XlsxParser.apply_edit(
                content=synthetic_formula_and_merge_workbook_bytes,
                sheet_name="Calc",
                coordinate=non_anchor,
                operation="xlsx.set_cell_value",
                value=777,
            )
        err = exc_info.value
        assert err.status_code == 422
        assert err.coordinate == non_anchor
        assert err.anchor == "C7"

    def test_adv_xlsx_merged_anchor_cell_edit_succeeds(
        self, synthetic_formula_and_merge_workbook_bytes
    ):
        """Editing anchor cell A5 (in A5:B5) must succeed."""
        updated = XlsxParser.apply_edit(
            content=synthetic_formula_and_merge_workbook_bytes,
            sheet_name="Calc",
            coordinate="A5",
            operation="xlsx.set_cell_value",
            value="ValidAnchorUpdate",
        )
        model = XlsxParser.parse(updated)
        cell = model.get_sheet("Calc").get_cell("A5")
        assert cell is not None
        assert cell.raw_value == "ValidAnchorUpdate"
        assert cell.is_merge_anchor is True


class TestXlsxFormulaPreservationAndFullCalc:
    """Verifies formula preservation, cached states, and fullCalcOnLoad guarantee."""

    def test_adv_xlsx_formula_expression_preservation(self, f01_f03_hidden_missing_cache_bytes):
        """Verify formula expression string <f> is preserved verbatim with data_only=False."""
        model = XlsxParser.parse(f01_f03_hidden_missing_cache_bytes)
        sheet = model.get_sheet("Visible")
        cell_b2 = sheet.get_cell("B2")
        assert cell_b2 is not None
        assert cell_b2.formula_expression == "=A2*2"
        assert cell_b2.cached_value is None
        assert cell_b2.cached_value_state == CachedValueState.MISSING

    def test_adv_xlsx_cached_value_present_in_f05(self, f05_synthetic_bytes):
        """Verify cached value states are PRESENT in f05."""
        model = XlsxParser.parse(f05_synthetic_bytes)
        meta_sheet = model.get_sheet("Meta")
        b9 = meta_sheet.get_cell("B9")
        assert b9 is not None
        assert b9.formula_expression == "=ROW()-1"
        assert b9.cached_value == 8
        assert b9.cached_value_state == CachedValueState.PRESENT

        b10 = meta_sheet.get_cell("B10")
        assert b10 is not None
        assert b10.formula_expression == "=COUNTA(Interview!A2:A13)"
        assert b10.cached_value == 12
        assert b10.cached_value_state == CachedValueState.PRESENT

    def test_adv_xlsx_full_calc_on_load_guaranteed_even_if_disabled_in_input(self):
        """fullCalcOnLoad must be explicitly True in memory and in the saved workbook.xml."""
        wb = openpyxl.Workbook()
        wb.calculation = CalcProperties(fullCalcOnLoad=False)
        ws = wb.active
        ws.title = "CalcCheck"
        ws["A1"] = 10
        buf = io.BytesIO()
        wb.save(buf)

        edited = XlsxParser.apply_edit(
            content=buf.getvalue(),
            sheet_name="CalcCheck",
            coordinate="A1",
            operation="xlsx.set_cell_value",
            value=99,
        )

        # 1. In-memory OpenPyXL verification
        wb_mod = openpyxl.load_workbook(io.BytesIO(edited), data_only=False)
        try:
            assert wb_mod.calculation is not None
            assert wb_mod.calculation.fullCalcOnLoad is True
        finally:
            wb_mod.close()

        # 2. Raw XML serialization verification inside xl/workbook.xml
        with zipfile.ZipFile(io.BytesIO(edited), "r") as z:
            wb_xml = z.read("xl/workbook.xml").decode("utf-8")
            assert "calcPr" in wb_xml
            assert 'fullCalcOnLoad="1"' in wb_xml or 'fullCalcOnLoad="true"' in wb_xml


# ============================================================================
# 2. DOCX OpenXML RunSpanMatcher Adversarial Challenges
# ============================================================================


def make_heterogeneous_5_run_paragraph(doc: docx.Document) -> docx.text.paragraph.Paragraph:
    """Constructs a paragraph with 5 runs of distinct, heterogeneous formatting:
    - Run 0: 'One ' (bold=True, Arial, 14pt, Color Red)
    - Run 1: 'Two ' (italic=True, Calibri, 12pt, Color Green)
    - Run 2: 'Three ' (underline=True, Times New Roman, 11pt, Color Blue)
    - Run 3: 'Four ' (bold=True, italic=True, Georgia, 16pt, Color Orange)
    - Run 4: 'Five.' (Consolas, 9pt, Color Gray)
    """
    p = doc.add_paragraph()

    r0 = p.add_run("One ")
    r0.bold = True
    r0.font.name = "Arial"
    r0.font.size = Pt(14)
    r0.font.color.rgb = RGBColor(255, 0, 0)

    r1 = p.add_run("Two ")
    r1.italic = True
    r1.font.name = "Calibri"
    r1.font.size = Pt(12)
    r1.font.color.rgb = RGBColor(0, 255, 0)

    r2 = p.add_run("Three ")
    r2.underline = True
    r2.font.name = "Times New Roman"
    r2.font.size = Pt(11)
    r2.font.color.rgb = RGBColor(0, 0, 255)

    r3 = p.add_run("Four ")
    r3.bold = True
    r3.italic = True
    r3.font.name = "Georgia"
    r3.font.size = Pt(16)
    r3.font.color.rgb = RGBColor(255, 165, 0)

    r4 = p.add_run("Five.")
    r4.font.name = "Consolas"
    r4.font.size = Pt(9)
    r4.font.color.rgb = RGBColor(128, 128, 128)

    return p


class TestDocxRunSpanMatcherAdversarial:
    """Adversarial stress-testing of DOCX OpenXML RunSpanMatcher."""

    def test_adv_docx_run_span_matcher_2_runs(self):
        """Match spans 2 runs (Run 0 'One ' and Run 1 'Two ').
        Replacement inherits Run 0's formatting; Run 1 preserves its suffix and style.
        """
        doc = docx.Document()
        p = make_heterogeneous_5_run_paragraph(doc)

        # Target spans from 'ne ' in Run 0 to 'Tw' in Run 1
        RunSpanMatcher.replace_in_paragraph(p, "ne Tw", "REPLACED_2")

        # Full text verification
        assert p.text == "OREPLACED_2o Three Four Five."

        # Opening run: 'O' + 'REPLACED_2', retains Arial, bold, 14pt, Red
        assert p.runs[0].text == "OREPLACED_2"
        assert p.runs[0].bold is True
        assert p.runs[0].font.name == "Arial"
        assert p.runs[0].font.size == Pt(14)
        assert p.runs[0].font.color.rgb == RGBColor(255, 0, 0)

        # Closing run: 'o ' preserved with Calibri, italic, 12pt, Green
        assert p.runs[1].text == "o "
        assert p.runs[1].italic is True
        assert p.runs[1].font.name == "Calibri"
        assert p.runs[1].font.size == Pt(12)
        assert p.runs[1].font.color.rgb == RGBColor(0, 255, 0)

        # Remaining runs untouched
        assert p.runs[2].text == "Three "
        assert p.runs[3].text == "Four "
        assert p.runs[4].text == "Five."

    def test_adv_docx_run_span_matcher_3_runs(self):
        """Match spans 3 runs (Run 1 'Two ', Run 2 'Three ', Run 3 'Four ').
        Opening Run 1 receives replacement (inheriting style);
        Intermediate Run 2 is cleared;
        Closing Run 3 preserves suffix and style.
        """
        doc = docx.Document()
        p = make_heterogeneous_5_run_paragraph(doc)

        # Target: 'wo Three Fou'
        RunSpanMatcher.replace_in_paragraph(p, "wo Three Fou", "REPLACED_3")

        assert p.text == "One TREPLACED_3r Five."

        # Run 0 untouched
        assert p.runs[0].text == "One "

        # Run 1: 'T' + 'REPLACED_3' inherits Calibri, italic, 12pt, Green
        assert p.runs[1].text == "TREPLACED_3"
        assert p.runs[1].italic is True
        assert p.runs[1].font.name == "Calibri"
        assert p.runs[1].font.size == Pt(12)
        assert p.runs[1].font.color.rgb == RGBColor(0, 255, 0)

        # Intermediate Run 2 cleared of text
        assert p.runs[2].text == ""

        # Closing Run 3: 'r ' suffix preserved with Georgia, bold, italic, 16pt, Orange
        assert p.runs[3].text == "r "
        assert p.runs[3].bold is True
        assert p.runs[3].italic is True
        assert p.runs[3].font.name == "Georgia"
        assert p.runs[3].font.size == Pt(16)
        assert p.runs[3].font.color.rgb == RGBColor(255, 165, 0)

        # Run 4 untouched
        assert p.runs[4].text == "Five."

    def test_adv_docx_run_span_matcher_5_runs(self):
        """Match spans all 5 runs (Run 0 through Run 4).
        Opening Run 0 receives replacement with its exact <w:rPr>;
        Intermediate runs 1, 2, 3 cleared;
        Closing Run 4 preserves suffix and its exact <w:rPr>.
        """
        doc = docx.Document()
        p = make_heterogeneous_5_run_paragraph(doc)

        # Target: 'ne Two Three Four Fiv'
        RunSpanMatcher.replace_in_paragraph(p, "ne Two Three Four Fiv", "<SPAN5>")

        assert p.text == "O<SPAN5>e."

        # Opening Run 0: 'O<SPAN5>', inherits Run 0 style
        assert p.runs[0].text == "O<SPAN5>"
        assert p.runs[0].bold is True
        assert p.runs[0].font.name == "Arial"
        assert p.runs[0].font.size == Pt(14)
        assert p.runs[0].font.color.rgb == RGBColor(255, 0, 0)

        # Intermediate runs 1, 2, 3 cleared
        assert p.runs[1].text == ""
        assert p.runs[2].text == ""
        assert p.runs[3].text == ""

        # Closing Run 4: 'e.' suffix preserved with Consolas, 9pt, Gray
        assert p.runs[4].text == "e."
        assert p.runs[4].font.name == "Consolas"
        assert p.runs[4].font.size == Pt(9)
        assert p.runs[4].font.color.rgb == RGBColor(128, 128, 128)

    @pytest.mark.parametrize("tag", ["w:ins", "w:del", "w:moveFrom", "w:moveTo"])
    def test_adv_docx_tracked_changes_fail_closed(self, tag: str):
        """Paragraphs containing tracked changes (<w:ins>, <w:del>, etc.) must fail closed with 422."""
        doc = docx.Document()
        p = doc.add_paragraph("Tracked paragraph text.")
        p._p.append(OxmlElement(tag))

        with pytest.raises(UnsupportedContentError) as exc_info:
            RunSpanMatcher.replace_in_paragraph(p, "Tracked", "Replaced")
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "docx_unsupported_content"
        assert "tracked changes" in str(exc_info.value).lower()

    @pytest.mark.parametrize("tag", ["w:fldSimple", "w:fldChar"])
    def test_adv_docx_complex_fields_fail_closed(self, tag: str):
        """Paragraphs containing complex Word fields (<w:fldSimple>, <w:fldChar>) must fail closed with 422."""
        doc = docx.Document()
        p = doc.add_paragraph("Field paragraph text.")
        p._p.append(OxmlElement(tag))

        with pytest.raises(UnsupportedContentError) as exc_info:
            RunSpanMatcher.replace_in_paragraph(p, "Field", "Replaced")
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "docx_unsupported_content"
        assert "complex word fields" in str(exc_info.value).lower()

    def test_adv_docx_hyperlink_intersection_fail_closed(self):
        """Matches intersecting hyperlinks (outside->inside, inside->out, entirely inside) must raise 422."""
        doc = docx.Document()
        p = doc.add_paragraph()
        p.add_run("Check ")

        # Hyperlink run
        h_elm = OxmlElement("w:hyperlink")
        r_h = OxmlElement("w:r")
        t_h = OxmlElement("w:t")
        t_h.text = "documentation page"
        r_h.append(t_h)
        h_elm.append(r_h)
        p._p.append(h_elm)

        p.add_run(" for full details.")

        # 1. Cross outside -> inside
        with pytest.raises(UnsupportedContentError) as exc1:
            RunSpanMatcher.replace_in_paragraph(p, "Check doc", "View doc")
        assert exc1.value.status_code == 422
        assert "hyperlink" in str(exc1.value).lower()

        # 2. Cross inside -> outside
        with pytest.raises(UnsupportedContentError) as exc2:
            RunSpanMatcher.replace_in_paragraph(p, "page for", "page and")
        assert exc2.value.status_code == 422

        # 3. Entirely inside hyperlink
        with pytest.raises(UnsupportedContentError) as exc3:
            RunSpanMatcher.replace_in_paragraph(p, "documentation", "guide")
        assert exc3.value.status_code == 422

        # 4. Completely outside hyperlink -> SUCCEEDS without damaging hyperlink
        RunSpanMatcher.replace_in_paragraph(p, "full details", "more specifications")
        assert p.text == "Check documentation page for more specifications."

    def test_adv_docx_ambiguous_matches_without_occurrence_raises_422(self):
        """Target appearing multiple times without explicit occurrence index must raise AmbiguousMatchError."""
        doc = docx.Document()
        p = doc.add_paragraph("item one item two item three")

        with pytest.raises(AmbiguousMatchError) as exc_info:
            RunSpanMatcher.replace_in_paragraph(p, "item", "product", occurrence=None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "docx_ambiguous_match"

    def test_adv_docx_occurrence_out_of_bounds_raises_422(self):
        """Occurrence index <= 0 or > count raises DocxFormatError (422)."""
        doc = docx.Document()
        p = doc.add_paragraph("alpha beta alpha gamma")

        for bad_occ in [0, -1, 3, 99]:
            with pytest.raises(DocxFormatError) as exc_info:
                RunSpanMatcher.replace_in_paragraph(p, "alpha", "delta", occurrence=bad_occ)
            assert exc_info.value.status_code == 422

    def test_adv_docx_empty_target_raises_422(self):
        """Empty target string raises DocxFormatError (422)."""
        doc = docx.Document()
        p = doc.add_paragraph("Non-empty text.")

        with pytest.raises(DocxFormatError) as exc_info:
            RunSpanMatcher.replace_in_paragraph(p, "", "something")
        assert exc_info.value.status_code == 422
        assert "target text cannot be empty" in str(exc_info.value).lower()

    def test_adv_docx_text_not_found_raises_404(self):
        """Search target not present raises TextNotFoundError (404)."""
        doc = docx.Document()
        p = doc.add_paragraph("Existing sentence.")

        with pytest.raises(TextNotFoundError) as exc_info:
            RunSpanMatcher.replace_in_paragraph(p, "phantom target", "replacement")
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "docx_text_not_found"

    def test_adv_docx_apply_edit_paragraph_bounds_raises_404(self, f01_docx_bytes):
        """DocxParser.apply_edit with out-of-range paragraph_order raises DocxFormatError (404)."""
        for bad_order in [0, -1, 9999]:
            with pytest.raises(DocxFormatError) as exc_info:
                DocxParser.apply_edit(
                    content=f01_docx_bytes,
                    paragraph_order=bad_order,
                    target_text="test",
                    replacement_text="test2",
                )
            assert exc_info.value.status_code == 404
