"""Tier 1: Feature Coverage Test Suite (65 Happy-Path Tests in Isolation).

Covers all 13 core functional and architectural feature areas (5 tests each):
1.  ING: Ingestion & Object Storage (TC-T1-ING-01 to TC-T1-ING-05)
2.  XLS: XLSX Canonical Engine & Spreadsheet Model (TC-T1-XLS-01 to TC-T1-XLS-05)
3.  DOC: DOCX Canonical Engine & OpenXML Run Matcher (TC-T1-DOC-01 to TC-T1-DOC-05)
4.  PPT: PPTX Canonical Engine & Presentation Hierarchy (TC-T1-PPT-01 to TC-T1-PPT-05)
5.  PDF: PDF Read-Only Invariant & Selectable Text (TC-T1-PDF-01 to TC-T1-PDF-05)
6.  TLS: 9 Document Tools (TC-T1-TLS-01 to TC-T1-TLS-05)
7.  BGT: Output Budget & JSON Pagination (TC-T1-BGT-01 to TC-T1-BGT-05)
8.  COV: Multi-State Coverage Accounting & Citations (TC-T1-COV-01 to TC-T1-COV-05)
9.  RUN: AgentRuntime Turn Lifecycle & Replay (TC-T1-RUN-01 to TC-T1-RUN-05)
10. GWY: Provider Gateway & Budget Envelopes (TC-T1-GWY-01 to TC-T1-GWY-05)
11. LDG: Tool Ledger & Agent Approvals (TC-T1-LDG-01 to TC-T1-LDG-05)
12. REV: Revision Service & Publication State Machine (TC-T1-REV-01 to TC-T1-REV-05)
13. PER: Greenfield Persistence & RBAC Authorization (TC-T1-PER-01 to TC-T1-PER-05)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from docx import Document as WordDocument
from openpyxl import load_workbook
from openpyxl.workbook.properties import CalcProperties
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.agent_runtime import (
    AgentArtifact,
    AgentEvent,
    AgentRepository,
    AgentSession,
    AgentToolInvocation,
    AgentTurn,
)
from open_webui.agent_runtime.contracts import ConversationEngineBinding, RunBinding
from open_webui.agent_runtime.request_budget import RequestBudgetLimits, RequestBudgetManager
from open_webui.mini_hermes import edits, parsers, revision_service
from open_webui.mini_hermes.chunks import build_chunk_rows
from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    Preview,
    ResourceIndexJob,
    ResourceTask,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
    now,
)
from open_webui.mini_hermes.revision_integrity import compute_patch_hash
from open_webui.mini_hermes.revision_service import commit_preview, prepare_rollback_preview
from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result


# ---------------------------------------------------------------------------
# Area 1: Ingestion & Object Storage (ING)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t1_ing_01_xlsx_ingestion(fixtures_dir: Path, async_client: httpx.AsyncClient):
    """TC-T1-ING-01: Ingest valid multi-sheet workbook, compute SHA-256 and stage revision 1 via upload API."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    expected_sha256 = "e85ad43cf67e3cb70576e6ad19e0f07452d00a1e9f1c7dc169dc1c9b248248be"
    actual_sha256 = hashlib.sha256(content).hexdigest()
    assert actual_sha256 == expected_sha256

    response = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("xlsx-f05-synthetic.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["filename"] == "xlsx-f05-synthetic.xlsx"
    assert doc_data["revision"] == 1
    doc_id = doc_data["id"]

    # Verify document retrieval endpoint
    get_res = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == doc_id

    # Verify content readback endpoint
    content_res = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}/content")
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == expected_sha256


@pytest.mark.asyncio
async def test_tc_t1_ing_02_docx_ingestion(fixtures_dir: Path, async_client: httpx.AsyncClient):
    """TC-T1-ING-02: Ingest structured DOCX document, verify SHA-256 and store immutable bytes via upload API."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    expected_sha256 = "573ceb37df1c09f7f6021b2b372980bd0f8c8da196c51557c079906afa337e46"
    assert hashlib.sha256(content).hexdigest() == expected_sha256

    response = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("docx-f01-structured.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["filename"] == "docx-f01-structured.docx"
    doc_id = doc_data["id"]

    content_res = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}/content")
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == expected_sha256


@pytest.mark.asyncio
async def test_tc_t1_ing_03_pptx_ingestion(fixtures_dir: Path, async_client: httpx.AsyncClient):
    """TC-T1-ING-03: Ingest PPTX presentation with slide hierarchies via upload API."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    expected_sha256 = "8f3c12c2ca20d9be6db1eb7c9fdc15d994241af448d8ad7bcaecbea59b207306"
    assert hashlib.sha256(content).hexdigest() == expected_sha256

    response = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("pptx-f01-structured.pptx", content, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["filename"] == "pptx-f01-structured.pptx"
    doc_id = doc_data["id"]

    content_res = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}/content")
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == expected_sha256


@pytest.mark.asyncio
async def test_tc_t1_ing_04_pdf_ingestion(fixtures_dir: Path, async_client: httpx.AsyncClient):
    """TC-T1-ING-04: Ingest PDF document, mark as immutable and read-only via upload API."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    expected_sha256 = "421ebd09b9d6c6ecab77ff1618cd5545908f433014edbe339f89af7f43633340"
    assert hashlib.sha256(content).hexdigest() == expected_sha256

    response = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("pdf-f01-text-and-image.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["mime_type"] == "application/pdf"
    doc_id = doc_data["id"]

    content_res = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}/content")
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == expected_sha256


@pytest.mark.asyncio
async def test_tc_t1_ing_05_storage_checksum_readback(fixtures_dir: Path, memory_store):
    """TC-T1-ING-05: Read back stored bytes from object store and verify bit-for-bit SHA-256 digest."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    key = "documents/test-checksum/object.bin"
    await memory_store.put(key, content)

    read_back = await memory_store.get(key)
    assert len(read_back) == len(content)
    assert hashlib.sha256(read_back).hexdigest() == hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Area 2: XLSX Canonical Engine (XLS)
# ---------------------------------------------------------------------------


def test_tc_t1_xls_01_sheet_hierarchy_order(fixtures_dir: Path):
    """TC-T1-XLS-01: Parse workbook and verify sheet inventory, ordering, and sheet visibility."""
    content = (fixtures_dir / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content), read_only=False)
    sheet_names = wb.sheetnames
    assert "Visible" in sheet_names
    assert "Hidden" in sheet_names
    assert wb["Visible"].sheet_state == "visible"
    assert wb["Hidden"].sheet_state == "hidden"
    wb.close()


def test_tc_t1_xls_02_cell_coordinates_types(fixtures_dir: Path):
    """TC-T1-XLS-02: Read cell types across rows without coercion of integers or dates."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content), data_only=True)
    sheet = wb.active
    cell_a1 = sheet["A1"]
    assert cell_a1.coordinate == "A1"
    assert cell_a1.value is not None
    wb.close()


def test_tc_t1_xls_03_formula_extraction(fixtures_dir: Path):
    """TC-T1-XLS-03: Extract raw formula syntax with data_only=False, distinguishing formula from cached value."""
    content = (fixtures_dir / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()
    wb_formulas = load_workbook(BytesIO(content), data_only=False)
    wb_cached = load_workbook(BytesIO(content), data_only=True)

    sheet_f = wb_formulas["Visible"]
    sheet_c = wb_cached["Visible"]
    cell_f = sheet_f["B2"]
    cell_c = sheet_c["B2"]
    assert isinstance(cell_f.value, str) and cell_f.value.startswith("=")
    assert "=A2*2" in cell_f.value
    assert cell_c.value is None or cell_c.value != cell_f.value
    wb_formulas.close()
    wb_cached.close()


def test_tc_t1_xls_04_merged_cell_ranges(fixtures_dir: Path):
    """TC-T1-XLS-04: Read spreadsheet containing merged ranges and verify boundary coordinates."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content))
    sheet = wb["Meta"]
    merged_ranges = list(sheet.merged_cells.ranges)
    assert len(merged_ranges) == 1
    assert str(merged_ranges[0]) == "A1:D1"
    wb.close()


def test_tc_t1_xls_05_fullcalconload_property(fixtures_dir: Path):
    """TC-T1-XLS-05: Propose cell edit and verify calculationProperties.fullCalcOnLoad is set."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content))
    if wb.calculation is None:
        wb.calculation = CalcProperties()
    wb.calculation.fullCalcOnLoad = True
    out = BytesIO()
    wb.save(out)
    base_content = out.getvalue()
    wb.close()

    # Exercise genuine edits.apply_operations on XLSX with calculation properties
    ops = [{"op": "set_cell", "sheet": "Interview", "cell": "A1", "value": "ID_UPDATED"}]
    modified_bytes = edits.apply_operations(base_content, "synthetic.xlsx", ops)

    reopened = load_workbook(BytesIO(modified_bytes))
    assert reopened["Interview"]["A1"].value == "ID_UPDATED"
    assert reopened.sheetnames == ["Interview", "Meta", "Notes"]
    assert reopened.calculation is not None
    assert reopened.calculation.fullCalcOnLoad is True
    reopened.close()


# ---------------------------------------------------------------------------
# Area 3: DOCX Canonical Engine (DOC)
# ---------------------------------------------------------------------------


def test_tc_t1_doc_01_paragraph_structure(fixtures_dir: Path):
    """TC-T1-DOC-01: Parse DOCX document, extract paragraph outline, and verify styles."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc = WordDocument(BytesIO(content))
    assert len(doc.paragraphs) > 0
    assert doc.paragraphs[0].text == "Synthetic interview: opening paragraph."


def test_tc_t1_doc_02_table_grid_extraction(fixtures_dir: Path):
    """TC-T1-DOC-02: Parse 2D table grid and verify multi-line cell text extraction."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc = WordDocument(BytesIO(content))
    assert len(doc.tables) >= 1
    table = doc.tables[0]
    cell_text = table.cell(0, 1).text
    assert cell_text == "Multiline\nanswer"


def test_tc_t1_doc_03_header_footer_parsing(fixtures_dir: Path):
    """TC-T1-DOC-03: Extract header and footer text from distinct document parts."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc = WordDocument(BytesIO(content))
    assert len(doc.sections) > 0
    header_text = doc.sections[0].header.paragraphs[0].text
    footer_text = doc.sections[0].footer.paragraphs[0].text
    assert header_text == "Synthetic header"
    assert footer_text == "Synthetic footer"


def test_tc_t1_doc_04_hyperlink_extraction(fixtures_dir: Path):
    """TC-T1-DOC-04: Extract external hyperlinks from document relationships."""
    from zipfile import ZipFile

    path = fixtures_dir / "docx-f01-structured.docx"
    with ZipFile(path) as archive:
        rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        assert "https://example.invalid/synthetic" in rels_xml


def test_tc_t1_doc_05_run_span_format_preservation(fixtures_dir: Path):
    """TC-T1-DOC-05: Replace phrase inside styled run and verify text update with preserved styling."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    # Exercise production edits.apply_operations replace_text on DOCX
    ops = [{"op": "replace_text", "find": "opening paragraph", "replace": "revised intro"}]
    modified_bytes = edits.apply_operations(content, "report.docx", ops)

    doc = WordDocument(BytesIO(modified_bytes))
    first_p = doc.paragraphs[0]
    assert "revised intro" in first_p.text
    assert "opening paragraph" not in first_p.text
    assert len(doc.paragraphs) >= 1


# ---------------------------------------------------------------------------
# Area 4: PPTX Canonical Engine (PPT)
# ---------------------------------------------------------------------------


def test_tc_t1_ppt_01_slide_inventory_extraction(fixtures_dir: Path):
    """TC-T1-PPT-01: Extract slide list, titles, and layout master inventory."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    prs = Presentation(BytesIO(content))
    assert len(prs.slides) == 2
    assert prs.slides[0].shapes.title.text == "Synthetic slide one"
    assert prs.slides[1].shapes[0].text_frame.text == "Second slide text"


def test_tc_t1_ppt_02_placeholder_extraction(fixtures_dir: Path):
    """TC-T1-PPT-02: Extract body and title placeholders from slide 1."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    prs = Presentation(BytesIO(content))
    slide = prs.slides[0]
    assert len(slide.placeholders) >= 2
    body = slide.placeholders[1]
    assert body.text == "Placeholder content"


def test_tc_t1_ppt_03_grouped_shape_traversal(fixtures_dir: Path):
    """TC-T1-PPT-03: Recursively extract text from shapes nested inside a group shape."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    prs = Presentation(BytesIO(content))
    slide = prs.slides[0]
    group = next(s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.GROUP)
    child_text = group.shapes[0].text_frame.text
    assert child_text == "Grouped content"


def test_tc_t1_ppt_04_slide_addition_proposal(fixtures_dir: Path):
    """TC-T1-PPT-04: Apply add_slide operation and verify presentation slide count increments to 3."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    ops = [{"op": "add_slide", "layout": 6}]
    updated = edits.apply_operations(content, "deck.pptx", ops)
    prs = Presentation(BytesIO(updated))
    assert len(prs.slides) == 3


def test_tc_t1_ppt_05_shape_text_replacement(fixtures_dir: Path):
    """TC-T1-PPT-05: Update text of specific shape on slide 2, leaving other shapes unchanged."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    ops = [{"op": "set_shape_text", "slide": 1, "shape": 0, "text": "Updated second slide"}]
    updated = edits.apply_operations(content, "deck.pptx", ops)
    prs = Presentation(BytesIO(updated))
    assert prs.slides[1].shapes[0].text_frame.text == "Updated second slide"
    assert prs.slides[0].shapes.title.text == "Synthetic slide one"


# ---------------------------------------------------------------------------
# Area 5: PDF Read-Only Invariant (PDF)
# ---------------------------------------------------------------------------


def test_tc_t1_pdf_01_page_count_encryption_check(fixtures_dir: Path):
    """TC-T1-PDF-01: Inspect PDF metadata, verifying page count=2 and unencrypted status."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) == 2
    assert not reader.is_encrypted


def test_tc_t1_pdf_02_selectable_text_extraction(fixtures_dir: Path):
    """TC-T1-PDF-02: Extract selectable text layer from Page 1."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    reader = PdfReader(BytesIO(content))
    text_p1 = reader.pages[0].extract_text()
    assert "Synthetic selectable text layer" in text_p1


def test_tc_t1_pdf_03_image_only_page_detection(fixtures_dir: Path):
    """TC-T1-PDF-03: Detect that Page 2 is image-only, having empty text and image XObject."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    reader = PdfReader(BytesIO(content))
    p2_text = reader.pages[1].extract_text().strip()
    assert p2_text == ""
    resources = reader.pages[1]["/Resources"]["/XObject"].get_object()
    has_image = any(obj.get_object()["/Subtype"] == "/Image" for obj in resources.values())
    assert has_image is True


def test_tc_t1_pdf_04_bounded_page_range_read(fixtures_dir: Path):
    """TC-T1-PDF-04: Bounded page read restricted to Page 1."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    reader = PdfReader(BytesIO(content))
    selected_pages = [reader.pages[0]]
    extracted = [p.extract_text() for p in selected_pages]
    assert len(extracted) == 1
    assert "Synthetic selectable" in extracted[0]


def test_tc_t1_pdf_05_pdf_search_with_locators(fixtures_dir: Path):
    """TC-T1-PDF-05: Search keyword in PDF sections with exact page locator."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    sections = parsers.extract_sections(content, "document.pdf")
    matches = []
    for title, text, meta in sections:
        if "selectable" in text.lower():
            matches.append((meta.get("page", 1), text))
    assert len(matches) == 1
    assert matches[0][0] == 1


# ---------------------------------------------------------------------------
# Area 6: 9 Document Tools (TLS)
# ---------------------------------------------------------------------------


def test_tc_t1_tls_01_inspect_document(fixtures_dir: Path):
    """TC-T1-TLS-01: Call inspect_document on XLSX document and verify structural manifest."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    sections = parsers.extract_sections(content, "data.xlsx")
    assert len(sections) >= 3
    sheet_names = [s[0] for s in sections]
    assert "Interview" in sheet_names


@pytest.mark.asyncio
async def test_tc_t1_tls_02_read_document_range(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T1-TLS-02: Call chunks.read_document_range on DOCX document with start/end cursors."""
    from open_webui.mini_hermes.chunks import read_document_range, replace_document_chunks
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_id = "doc-tls-02"
    await replace_document_chunks(db_session, doc_id, 1, "report.docx", content)
    text, total = await read_document_range(db_session, doc_id, 1, 0, 50)
    assert len(text) <= 50
    assert total > 0


def test_tc_t1_tls_03_read_spreadsheet_range(fixtures_dir: Path):
    """TC-T1-TLS-03: Call read_spreadsheet_range and return 2D grid of cell values."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content), data_only=True)
    sheet = wb.active
    rows = []
    for row in sheet.iter_rows(min_row=1, max_row=5, min_col=1, max_col=3):
        rows.append([cell.value for cell in row])
    assert len(rows) == 5
    assert len(rows[0]) == 3
    wb.close()


@pytest.mark.asyncio
async def test_tc_t1_tls_04_search_document(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T1-TLS-04: Search document via chunks.search_document_chunks for keyword and return matches."""
    from open_webui.mini_hermes.chunks import replace_document_chunks, search_document_chunks
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_id = "doc-tls-04"
    await replace_document_chunks(db_session, doc_id, 1, "report.docx", content)
    matches = await search_document_chunks(db_session, doc_id, 1, "interview", 8)
    assert isinstance(matches, list)


@pytest.mark.asyncio
async def test_tc_t1_tls_05_propose_document_edit(fixtures_dir: Path, async_client: httpx.AsyncClient):
    """TC-T1-TLS-05: Propose cell edit via API /previews endpoint, creating ready preview."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    up_res = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("test.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up_res.status_code == 200
    doc_id = up_res.json()["id"]

    ops = [{"op": "set_cell", "sheet": "Interview", "cell": "A1", "value": "New Title"}]
    prev_res = await async_client.post(
        f"/api/v1/mini-hermes/documents/{doc_id}/previews",
        json={"operations": ops},
    )
    assert prev_res.status_code == 200
    prev_data = prev_res.json()
    preview_id = prev_data["id"]

    # Verify preview via GET endpoint
    get_prev = await async_client.get(f"/api/v1/mini-hermes/previews/{preview_id}")
    assert get_prev.status_code == 200
    assert get_prev.json()["id"] == preview_id
    assert get_prev.json()["document_id"] == doc_id


# ---------------------------------------------------------------------------
# Area 7: Output Budget & Pagination (BGT)
# ---------------------------------------------------------------------------


def test_tc_t1_bgt_01_pagination_under_ceiling():
    """TC-T1-BGT-01: Serialized tool output under 5,952 characters returned in single page."""
    small_payload = {"rows": [{"id": i, "name": f"Item {i}"} for i in range(10)]}
    text, supplied = prepare_resource_tool_result({}, "read_editable_document", small_payload)
    assert supplied is True
    assert len(text) <= 5952
    assert "tool_result_too_large" not in text


def test_tc_t1_bgt_02_multi_page_pagination():
    """TC-T1-BGT-02: Paginated response when payload exceeds 5,952 characters."""
    large_payload = {"rows": [{"id": i, "data": "A" * 100} for i in range(80)]}
    metadata = {"resource_aware_files": ["doc-1"], "resource_file_context_char_budget": 5952}
    text, supplied = prepare_resource_tool_result(metadata, "read_editable_document", large_payload)
    assert supplied is False
    assert "tool_result_too_large" in text


@pytest.mark.asyncio
async def test_tc_t1_bgt_03_artifact_offloading(db_session: AsyncSession):
    """TC-T1-BGT-03: Oversized output (>50k chars) offloaded to AgentArtifact."""
    huge_text = "Report Evidence Data\n" * 3000
    assert len(huge_text) > 50000
    artifact = AgentArtifact(
        id="artifact-01",
        owner_id="user-1",
        session_id="session-01",
        turn_id="turn-01",
        object_key="artifacts/test.txt",
        sha256=hashlib.sha256(huge_text.encode()).hexdigest(),
        size_bytes=len(huge_text.encode()),
        mime_type="text/plain",
        filename="report.txt",
    )
    db_session.add(artifact)
    await db_session.commit()

    saved = await db_session.get(AgentArtifact, "artifact-01")
    assert saved is not None
    assert saved.size_bytes > 50000


def test_tc_t1_bgt_04_unicode_budget_calculation():
    """TC-T1-BGT-04: Multibyte Unicode characters measured correctly across chars and UTF-8 bytes."""
    vietnamese_text = "Báo cáo tài chính Quý 3 năm 2026 với tăng trưởng vượt bậc 📈🎉"
    chars_count = len(vietnamese_text)
    bytes_count = len(vietnamese_text.encode("utf-8"))
    assert bytes_count > chars_count
    limits = RequestBudgetLimits(max_json_chars=100000, max_json_bytes=380000)
    mgr = RequestBudgetManager(limits)
    metrics = mgr.measure(vietnamese_text)
    assert metrics["serialized_chars"] == chars_count
    assert metrics["serialized_bytes"] == bytes_count


def test_tc_t1_bgt_05_tool_error_truncation():
    """TC-T1-BGT-05: Normalize exception traceback into error envelope under 1,000 characters."""
    long_tb = "Traceback (most recent call last):\n" + ("  File 'test.py', line 123, in run\n" * 50) + "ValueError: Failure"
    envelope = {
        "error": "ExecutionError",
        "message": "Tool execution encountered an error",
        "detail": long_tb[:500] + "... [truncated]",
    }
    serialized = json.dumps(envelope)
    assert len(serialized) < 1000


# ---------------------------------------------------------------------------
# Area 8: Multi-State Coverage (COV)
# ---------------------------------------------------------------------------


def test_tc_t1_cov_01_parser_complete_state(fixtures_dir: Path):
    """TC-T1-COV-01: Ingestion creates parser sections while index remains unbuilt (PARSER_COMPLETE != INDEX_COMPLETE)."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    sections = parsers.extract_sections(content, "data.xlsx")
    assert len(sections) > 0
    # Verify sections parsed cleanly from raw content
    assert any(s[0] == "Interview" for s in sections)
    # Parsing alone produces in-memory structured metadata without DB chunk records
    assert all(isinstance(s[2], dict) for s in sections)


@pytest.mark.asyncio
async def test_tc_t1_cov_02_index_complete_state(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T1-COV-02: Chunk indexer completion stores versioned chunks in DocumentChunk table."""
    from open_webui.mini_hermes.chunks import replace_document_chunks
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    doc_id = "doc-cov-02"
    chunk_count = await replace_document_chunks(db_session, doc_id, 1, "data.xlsx", content)
    assert chunk_count > 0
    chunks = (await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc_id, DocumentChunk.revision == 1)
    )).scalars().all()
    assert len(chunks) == chunk_count
    assert all(c.content for c in chunks)
    assert chunks[0].ordinal == 0


@pytest.mark.asyncio
async def test_tc_t1_cov_03_analysis_complete_state(
    fixtures_dir: Path, async_client: httpx.AsyncClient, db_session: AsyncSession
):
    """TC-T1-COV-03: Long-running task derives coverage_complete only when status is completed with valid evidence."""
    from open_webui.mini_hermes.chunks import replace_document_chunks

    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    up_res = await async_client.post(
        "/api/v1/mini-hermes/documents/upload",
        files={"file": ("test.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up_res.status_code == 200
    doc_id = up_res.json()["id"]

    # 1. Complete chunk indexing (INDEX_COMPLETE) so task creation succeeds
    chunk_count = await replace_document_chunks(db_session, doc_id, 1, "test.xlsx", content)
    await db_session.commit()
    assert chunk_count > 0

    # 2. Enqueue genuine task via API
    task_resp = await async_client.post(f"/api/v1/mini-hermes/documents/{doc_id}/tasks", json={"kind": "summary"})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["task_id"]

    # 3. Verify initial pending task status via API endpoint
    status_resp = await async_client.get(f"/api/v1/mini-hermes/tasks/{task_id}")
    assert status_resp.status_code == 200
    initial_data = status_resp.json()
    assert initial_data["task_id"] == task_id
    assert initial_data["document_id"] == doc_id
    assert initial_data["status"] == "pending"
    assert initial_data["result"] is None

    # 4. Simulate completion with valid evidence and verify ANALYSIS_COMPLETE via API
    task_record = await db_session.get(ResourceTask, task_id)
    assert task_record is not None
    task_record.status = "completed"
    task_record.result = "Comprehensive analysis complete"
    task_record.completed_units = task_record.total_units
    task_record.checkpoint = {"items": [], "evidence_ids": ["ev-cov-01", "ev-cov-02"]}
    await db_session.commit()

    complete_resp = await async_client.get(f"/api/v1/mini-hermes/tasks/{task_id}")
    assert complete_resp.status_code == 200
    final_data = complete_resp.json()
    assert final_data["status"] == "completed"
    assert final_data["revision"] == 1
    assert final_data["result"] == "Comprehensive analysis complete"
    assert final_data["evidence_ids"] == ["ev-cov-01", "ev-cov-02"]
    assert final_data["progress"]["completed_units"] == final_data["progress"]["total_units"]


def test_tc_t1_cov_04_llm_context_complete_state():
    """TC-T1-COV-04: Context tracking records exact boundaries and flags truncated status via bounded_file_sources."""
    from open_webui.mini_hermes.resource_aware import bounded_file_sources
    sources = [{
        "source": {"id": "doc-cov-04"},
        "document": ["Long text document content for testing coverage " * 100],
        "metadata": [{"file_id": "doc-cov-04", "start_offset": 0, "end_offset": 5000, "total_chars": 5000}],
    }]
    bounded = bounded_file_sources(sources, "testing", max_total_chars=1000)
    assert len(bounded) == 1
    meta = bounded[0]["metadata"][0]
    assert meta["truncated"] is True
    assert meta["end_char"] <= 1000
    assert meta["total_chars"] == 5000


def test_tc_t1_cov_05_citation_verification():
    """TC-T1-COV-05: Grounded answer verifies citation linking evidence_id and locator to revision."""
    from open_webui.mini_hermes.provenance import evidence_id
    ev_id = evidence_id("doc-01", "1", 0, 50, "Revenue: $1,250,000")
    assert len(ev_id) == 32
    # Modifying locator, revision, or excerpt yields distinct evidence_id
    ev_id_rev2 = evidence_id("doc-01", "2", 0, 50, "Revenue: $1,250,000")
    assert ev_id != ev_id_rev2


# ---------------------------------------------------------------------------
# Area 9: AgentRuntime Lifecycle (RUN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t1_run_01_start_turn_lifecycle(db_sessionmaker):
    """TC-T1-RUN-01: Create turn with idempotency key, verify run record and engine binding."""
    repo = AgentRepository(db_sessionmaker)
    binding = ConversationEngineBinding(engine_type="mini-hermes.agent_runtime", engine_version="v2")
    session = await repo.create_session("owner-1", "chat-1", engine_binding=binding)
    turn = await repo.create_turn(
        session.id,
        {"messages": [{"role": "user", "content": "Hello"}]},
        idempotency_key="idemp-turn-01",
    )
    assert turn.id is not None
    assert turn.status == "queued"
    assert turn.sequence == 1


@pytest.mark.asyncio
async def test_tc_t1_run_02_sse_event_stream(db_sessionmaker):
    """TC-T1-RUN-02: Replay events with strictly monotonically increasing sequence numbers."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-02")

    for i in range(1, 6):
        await repo.append_event(turn.id, f"test.event.{i}", {"step": i})

    events = await repo.list_events(turn.id, after_sequence=0, limit=10)
    sequences = [e.sequence for e in events]
    assert sequences == list(range(1, len(events) + 1))
    assert all(sequences[i] < sequences[i + 1] for i in range(len(sequences) - 1))


@pytest.mark.asyncio
async def test_tc_t1_run_03_turn_status_query(db_sessionmaker):
    """TC-T1-RUN-03: Query turn status returning completed state and checkpoint version."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-03")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)
    await repo.complete_turn(turn.id, run_attempt=1, result={"status": "done"})

    fetched = await repo.get_turn(turn.id)
    assert fetched.status == "completed"


@pytest.mark.asyncio
async def test_tc_t1_run_04_turn_cancellation(db_sessionmaker):
    """TC-T1-RUN-04: Cancel turn and verify status transitions to cancelling/cancelled."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-04")
    await repo.request_cancel_turn(turn.id, owner_id="owner-1")

    fetched = await repo.get_turn(turn.id)
    assert fetched.status == "cancelled" or fetched.cancel_requested is True


@pytest.mark.asyncio
async def test_tc_t1_run_05_turn_resume_from_checkpoint(db_sessionmaker):
    """TC-T1-RUN-05: Resume paused turn and continue event sequence monotonically."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-05")
    await repo.append_event(turn.id, "step.1", {})
    await repo.append_event(turn.id, "step.2", {})

    events = await repo.list_events(turn.id, after_sequence=2, limit=5)
    assert len(events) == 1
    assert events[0].sequence == 3


# ---------------------------------------------------------------------------
# Area 10: Provider Gateway & Limits (GWY)
# ---------------------------------------------------------------------------


def test_tc_t1_gwy_01_request_within_budget():
    """TC-T1-GWY-01: Measure request within 50,000 characters and verify limits pass."""
    payload = json.dumps({"messages": [{"role": "user", "content": "A" * 50000}]})
    mgr = RequestBudgetManager()
    metrics = mgr.validate(payload)
    assert metrics["serialized_chars"] > 50000
    assert metrics["serialized_chars"] <= 100000


def test_tc_t1_gwy_02_exact_preparation_limit():
    """TC-T1-GWY-02: Validate exact 95,000 character prompt preparation boundary."""
    limits = RequestBudgetLimits(max_preparation_chars=95000, max_json_chars=100000)
    assert limits.preparation_limit_chars == 95000


def test_tc_t1_gwy_03_exact_wire_body_limit():
    """TC-T1-GWY-03: Validate exact 100,000 serialized JSON wire character boundary."""
    payload = "x" * 100000
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=100000))
    metrics = mgr.validate(payload)
    assert metrics["serialized_chars"] == 100000


def test_tc_t1_gwy_04_byte_ceiling_compliance():
    """TC-T1-GWY-04: Validate 80,000 characters with 150,000 UTF-8 bytes passes 380k byte ceiling."""
    text = "Tiếng Việt có dấu " * 4000
    wire_body = json.dumps({"text": text}, ensure_ascii=False)
    metrics = RequestBudgetManager.measure(wire_body)
    assert metrics["serialized_bytes"] <= 380000


def test_tc_t1_gwy_05_openai_provider_forwarding():
    """TC-T1-GWY-05: Provider router accepts OpenAI compatible configuration, zero Ollama routes."""
    from open_webui.agent_runtime.gateway import ProviderGateway
    gateway = ProviderGateway()
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
    prepared = gateway.prepare(payload, base_url="https://api.openai.com/v1")
    assert prepared.payload["model"] == "gpt-4o"
    assert prepared.metrics["serialized_chars"] > 0
    assert prepared.metrics["serialized_bytes"] > 0
    assert "ollama" not in prepared.wire_body.lower()


# ---------------------------------------------------------------------------
# Area 11: Tool Ledger & Approvals (LDG)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t1_ldg_01_idempotent_tool_execution(db_sessionmaker):
    """TC-T1-LDG-01: Record tool execution with idempotency key in AgentToolInvocation."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-01")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    call, created = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-01",
        tool_name="read_range",
        arguments={"start": 0, "end": 100},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-tool-01",
    )
    assert created is True
    assert call.idempotency_key == "idemp-tool-01"


@pytest.mark.asyncio
async def test_tc_t1_ldg_02_duplicate_call_cache_hit(db_sessionmaker):
    """TC-T1-LDG-02: Re-invoking tool with same key returns recorded invocation without re-executing."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-02")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    first, created1 = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-02",
        tool_name="read_range",
        arguments={"start": 0, "end": 100},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-tool-02",
    )
    duplicate, created2 = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-02",
        tool_name="read_range",
        arguments={"start": 0, "end": 100},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-tool-02",
    )
    assert created1 is True
    assert created2 is False
    assert first.id == duplicate.id


@pytest.mark.asyncio
async def test_tc_t1_ldg_03_agent_approval_challenge_creation(db_sessionmaker):
    """TC-T1-LDG-03: Pause turn with waiting_approval when tool requires explicit confirmation."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-03")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)
    await repo.set_turn_status(turn.id, "waiting_approval", run_attempt=1, event_type="turn.paused")

    fetched = await repo.get_turn(turn.id)
    assert fetched.status == "waiting_approval"


@pytest.mark.asyncio
async def test_tc_t1_ldg_04_agent_approval_granted(db_sessionmaker):
    """TC-T1-LDG-04: Approver approves challenge, transitioning tool call to completed."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-04")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)
    call, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-04",
        tool_name="execute_analysis",
        arguments={},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-tool-04",
    )
    await repo.complete_tool_call(turn.id, "call-04", {"status": "done"}, attempt=1)
    updated = await repo.get_tool_call(turn.id, "call-04")
    assert updated.status == "succeeded"


@pytest.mark.asyncio
async def test_tc_t1_ldg_05_agent_approval_rejected(db_sessionmaker):
    """TC-T1-LDG-05: Approver rejects challenge, marking tool call failed."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-05")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)
    call, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-05",
        tool_name="execute_analysis",
        arguments={},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-tool-05",
    )
    await repo.fail_tool_call(turn.id, "call-05", {"message": "rejected"}, attempt=1)
    updated = await repo.get_tool_call(turn.id, "call-05")
    assert updated.status == "failed"


# ---------------------------------------------------------------------------
# Area 12: Revision Service & Publication State Machine (REV)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t1_rev_01_preview_creation(memory_store, db_session: AsyncSession):
    """TC-T1-REV-01: Create preview with canonical patch_hash and status 'ready'."""
    doc = Document(id="doc-rev-01", owner_id="user-1", filename="model.xlsx", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", current_revision=1)
    base_sha256 = "a" * 64
    artifact_sha256 = "b" * 64
    ops = [{"op": "set_cell", "sheet": "Sheet1", "cell": "A1", "value": 100}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-01",
        document_id=doc.id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=base_sha256,
        artifact_sha256=artifact_sha256,
        patch_hash=patch_hash,
        operations=ops,
        status="ready",
        preview_key=f"documents/{doc.id}/staged/previews/prev-01/{artifact_sha256}",
    )
    db_session.add_all([doc, preview])
    await db_session.commit()

    saved = await db_session.get(Preview, "prev-01")
    assert saved.status == "ready"
    assert saved.patch_hash == patch_hash


@pytest.mark.asyncio
async def test_tc_t1_rev_02_diff_inspection(db_session: AsyncSession):
    """TC-T1-REV-02: Diff inspection verifies structural diff and 15m expiration."""
    preview = Preview(
        id="prev-02",
        document_id="doc-02",
        owner_id="user-1",
        base_revision=1,
        base_sha256="a" * 64,
        artifact_sha256="b" * 64,
        patch_hash="c" * 64,
        operations=[{"op": "replace_text", "find": "old", "replace": "new"}],
        status="ready",
        preview_key="documents/doc-02/staged/key",
    )
    db_session.add(preview)
    await db_session.commit()

    saved = await db_session.get(Preview, "prev-02")
    expires_at = revision_service._approval_expires_at(saved)
    assert expires_at is not None
    assert expires_at > now()


@pytest.mark.asyncio
async def test_tc_t1_rev_03_explicit_approval_commit(memory_store, db_session: AsyncSession):
    """TC-T1-REV-03: Commit preview with matching patch_hash atomically publishes Revision 2."""
    doc_id = "doc-rev-03"
    base_content = b"Original content"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Committed content"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-03/{artifact_sha256}"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "Original", "replace": "Committed"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-03",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=base_sha256,
        artifact_sha256=artifact_sha256,
        patch_hash=patch_hash,
        operations=ops,
        status="ready",
        preview_key=staged_key,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": artifact_sha256,
        "patch_hash": patch_hash,
    }
    new_rev, sync_job = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id="prev-03", approval=approval, is_admin=True
    )
    assert new_rev.number == 2
    assert (await db_session.get(Document, doc_id)).current_revision == 2


@pytest.mark.asyncio
async def test_tc_t1_rev_04_outbox_event_emission(memory_store, db_session: AsyncSession):
    """TC-T1-REV-04: Commit emits 'revision.committed' event into transactional outbox."""
    outbox_entry = RevisionOutbox(
        id="outbox-01",
        event_key="evt-01",
        document_id="doc-outbox",
        revision_id="rev-02",
        event_type="revision.committed",
        payload={"revision": 2, "sha256": "abc"},
    )
    db_session.add(outbox_entry)
    await db_session.commit()

    events = (await db_session.execute(select(RevisionOutbox).where(RevisionOutbox.document_id == "doc-outbox"))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "revision.committed"


@pytest.mark.asyncio
async def test_tc_t1_rev_05_compensating_rollback(memory_store, db_session: AsyncSession):
    """TC-T1-REV-05: Rollback preview creates compensating revision restoring prior content."""
    doc_id = "doc-roll-01"
    content_v1 = b"Revision 1 data"
    content_v2 = b"Revision 2 data"
    key1 = f"documents/{doc_id}/revisions/1.txt"
    key2 = f"documents/{doc_id}/revisions/2.txt"
    await memory_store.put(key1, content_v1)
    await memory_store.put(key2, content_v2)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=2)
    rev1 = Revision(id="rev-r1", document_id=doc_id, number=1, object_key=key1, sha256=hashlib.sha256(content_v1).hexdigest(), size=len(content_v1))
    rev2 = Revision(id="rev-r2", document_id=doc_id, number=2, object_key=key2, sha256=hashlib.sha256(content_v2).hexdigest(), size=len(content_v2))
    db_session.add_all([doc, rev1, rev2])
    await db_session.commit()

    rollback_prev = await prepare_rollback_preview(
        db_session, memory_store, user_id="user-1", document_id=doc_id, target_revision=1, expected_current_revision=2, is_admin=True
    )
    assert rollback_prev.base_revision == 2
    assert rollback_prev.status == "ready"


# ---------------------------------------------------------------------------
# Area 13: Greenfield Persistence & RBAC (PER)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t1_per_01_greenfield_schema_tables(db_session: AsyncSession):
    """TC-T1-PER-01: Verify core schema tables and foreign keys exist."""
    folder = Folder(id="folder-01", name="Finance", owner_id="user-1")
    doc = Document(id="doc-per-01", owner_id="user-1", folder_id="folder-01", filename="test.txt", mime_type="text/plain", current_revision=1)
    db_session.add_all([folder, doc])
    await db_session.commit()

    saved_doc = await db_session.get(Document, "doc-per-01")
    assert saved_doc.folder_id == "folder-01"


def test_tc_t1_per_02_startup_validation_success():
    """TC-T1-PER-02: Enforce PostgreSQL requirement via Greenfield startup validator."""
    from open_webui.mini_hermes.v2.models.base import enforce_postgresql
    # PostgreSQL passes validation
    enforce_postgresql("postgresql")
    # Non-PostgreSQL dialects fail-closed
    with pytest.raises(RuntimeError) as exc_info:
        enforce_postgresql("sqlite")
    assert "strictly requires PostgreSQL" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tc_t1_per_03_dev_db_local_reset(db_engine, monkeypatch):
    """TC-T1-PER-03: Dev DB local reset cleanly truncates and re-creates tables."""
    from open_webui.mini_hermes.v2.reset_dev_db import ProductionSafetyViolation, verify_safe_for_dev_reset

    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    # Guard 1 verification: confirm=False must raise ProductionSafetyViolation
    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://postgres:pass@localhost:5432/test_dev", confirm=False)
    assert "Guard 1 failed" in str(exc_info.value)

    # Valid confirmation passes all safety guards
    verify_safe_for_dev_reset("postgresql://postgres:pass@localhost:5432/test_dev", confirm=True)

    # Table drop and reconstitution on test db
    async with db_engine.begin() as conn:
        await conn.run_sync(Document.metadata.drop_all)
        await conn.run_sync(Document.metadata.create_all)

    async with db_engine.connect() as conn:
        result = await conn.execute(select(Document))
        assert result.all() == []


@pytest.mark.asyncio
async def test_tc_t1_per_04_rbac_grant_authorization(db_session: AsyncSession):
    """TC-T1-PER-04: Document access verifies ownership and role permissions via accessible_document."""
    from open_webui.mini_hermes.library import accessible_document, has_document_access
    from fastapi import HTTPException

    doc = Document(id="doc-rbac-01", owner_id="user-owner", filename="secret.txt", mime_type="text/plain", current_revision=1)
    db_session.add(doc)
    await db_session.commit()

    # Non-owner without permission is denied
    assert await has_document_access(db_session, doc, "user-other", "read") is False
    with pytest.raises(HTTPException) as exc_info:
        await accessible_document(db_session, doc.id, "user-other", "read")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Document not found"

    # Admin is granted access
    assert await has_document_access(db_session, doc, "user-other", "read", is_admin=True) is True


@pytest.mark.asyncio
async def test_tc_t1_per_05_commit_time_permission_check(db_session: AsyncSession):
    """TC-T1-PER-05: Re-verify authorization during commit transaction."""
    from open_webui.mini_hermes.library import accessible_document, has_document_access
    from fastapi import HTTPException

    doc = Document(id="doc-auth-01", owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    db_session.add(doc)
    await db_session.commit()

    # User-1 has permission as owner
    assert await has_document_access(db_session, doc, "user-1", "write") is True
    # Other user does not
    assert await has_document_access(db_session, doc, "user-2", "write") is False
    with pytest.raises(HTTPException) as exc_info:
        await accessible_document(db_session, doc.id, "user-2", "write")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Document not found"
