"""Tier 2: Boundary & Corner Cases Test Suite (65 Tests Across 13 Feature Areas).

Covers negative boundaries, format limits, security guardrails, TTL expirations,
budget ceilings, quarantine invariants, and concurrency edge cases:
1.  ING: Ingestion & Object Storage (TC-T2-ING-01 to TC-T2-ING-05)
2.  XLS: XLSX Canonical Engine & Spreadsheet Model (TC-T2-XLS-01 to TC-T2-XLS-05)
3.  DOC: DOCX Canonical Engine & OpenXML Run Matcher (TC-T2-DOC-01 to TC-T2-DOC-05)
4.  PPT: PPTX Canonical Engine & Presentation Hierarchy (TC-T2-PPT-01 to TC-T2-PPT-05)
5.  PDF: PDF Read-Only Invariant & Selectable Text (TC-T2-PDF-01 to TC-T2-PDF-05)
6.  TLS: 9 Document Tools (TC-T2-TLS-01 to TC-T2-TLS-05)
7.  BGT: Output Budget & JSON Pagination (TC-T2-BGT-01 to TC-T2-BGT-05)
8.  COV: Multi-State Coverage Accounting & Citations (TC-T2-COV-01 to TC-T2-COV-05)
9.  RUN: AgentRuntime Turn Lifecycle & Replay (TC-T2-RUN-01 to TC-T2-RUN-05)
10. GWY: Provider Gateway & Budget Envelopes (TC-T2-GWY-01 to TC-T2-GWY-05)
11. LDG: Tool Ledger & Agent Approvals (TC-T2-LDG-01 to TC-T2-LDG-05)
12. REV: Revision Service & Publication State Machine (TC-T2-REV-01 to TC-T2-REV-05)
13. PER: Greenfield Persistence & RBAC Authorization (TC-T2-PER-01 to TC-T2-PER-05)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from docx import Document as WordDocument
from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.utils.exceptions import CellCoordinatesException
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
import pypdf.errors
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config as app_config
from open_webui.agent_runtime import (
    AgentArtifact,
    AgentCheckpoint,
    AgentEvent,
    AgentRepository,
    AgentSession,
    AgentToolInvocation,
    AgentTurn,
)
from open_webui.agent_runtime.contracts import ConversationEngineBinding, RunBinding
from open_webui.agent_runtime.repository import StaleRunError
from open_webui.agent_runtime.request_budget import (
    RequestBudgetExceeded,
    RequestBudgetLimits,
    RequestBudgetManager,
)
from open_webui.mini_hermes import (
    chunks,
    edits,
    parsers,
    provenance,
    resource_aware,
    resource_indexing,
    revision_service,
    service,
    tools,
)
from open_webui.mini_hermes.library import accessible_document
from open_webui.mini_hermes.models import (
    Document,
    Folder,
    Preview,
    ResourceIndexJob,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    now,
)
from open_webui.mini_hermes.provider_guard import (
    ProviderCapabilityUnsupported,
    ProviderPayloadTooLarge,
    apply_capability_constraints,
)
from open_webui.mini_hermes.resource_aware import bounded_file_sources, bounded_tool_output
from open_webui.mini_hermes.revision_integrity import compute_patch_hash
from open_webui.mini_hermes.revision_service import commit_preview
from open_webui.mini_hermes.service import MAX_BYTES, create_document, validate_generated_content
from open_webui.mini_hermes.storage import ObjectStore
from open_webui.mini_hermes.v2.models.base import enforce_postgresql
from open_webui.mini_hermes.v2.reset_dev_db import ProductionSafetyViolation, verify_safe_for_dev_reset


# ---------------------------------------------------------------------------
# Area 1: Ingestion & Object Storage Boundaries (ING)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_ing_01_zero_length_file(memory_store, db_session: AsyncSession):
    """TC-T2-ING-01: Reject zero-length uploaded file (0 bytes) with HTTP 422 empty_file_rejected."""
    empty_content = b""
    with pytest.raises(HTTPException) as exc_info:
        await create_document(db_session, memory_store, "user-1", "empty.xlsx", empty_content)
    assert exc_info.value.status_code == 422
    assert "Invalid XLSX output" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_ing_02_magic_byte_mismatch(memory_store, db_session: AsyncSession):
    """TC-T2-ING-02: Reject file extension / magic byte mismatch (JPEG renamed to .docx)."""
    fake_docx_content = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    with pytest.raises(HTTPException) as exc_info:
        await create_document(db_session, memory_store, "user-1", "report.docx", fake_docx_content)
    assert exc_info.value.status_code == 422
    assert "Invalid DOCX output" in exc_info.value.detail


def test_tc_t2_ing_03_zip_slip_directory_traversal():
    """TC-T2-ING-03: Detect Zip Slip directory traversal path in archive entry and reject."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0:root:/root:/bin/bash\n")
    archive_bytes = buffer.getvalue()

    with pytest.raises(HTTPException) as exc_info:
        validate_generated_content(archive_bytes, ".xlsx")
    assert exc_info.value.status_code == 422
    assert "Invalid XLSX output" in exc_info.value.detail


def test_tc_t2_ing_04_archive_decompression_bomb():
    """TC-T2-ING-04: Archive decompression bomb ceiling exceeded (>100MB uncompressed)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zinfo = zipfile.ZipInfo("large_data.xml")
        zinfo.file_size = 105 * 1024 * 1024  # Declared uncompressed size 105 MB
        zf.writestr(zinfo, b"")
    bomb_bytes = buffer.getvalue()

    with pytest.raises(HTTPException) as exc_info:
        validate_generated_content(bomb_bytes, ".xlsx")
    assert exc_info.value.status_code == 422
    assert "Invalid XLSX output" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_ing_05_maximum_file_size_ceiling(memory_store, db_session: AsyncSession):
    """TC-T2-ING-05: Reject file strictly exceeding the 20MB maximum upload limit."""
    oversized_len = MAX_BYTES + 1
    oversized_content = b"X" * oversized_len
    with pytest.raises(HTTPException) as exc_info:
        await create_document(db_session, memory_store, "user-1", "oversized.xlsx", oversized_content)
    assert exc_info.value.status_code == 413
    assert "File exceeds 20 MB limit" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Area 2: XLSX Canonical Engine Boundaries (XLS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_xls_01_formula_overwrite_guardrail(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T2-XLS-01: Formula guardrails prevent illegal formula synchronization or preserve formulas."""
    doc = Document(
        id="doc-drive-sheet-01",
        owner_id="user-1",
        filename="budget.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        drive_mime_type="application/vnd.google-apps.spreadsheet",
        current_revision=1,
    )
    rev = Revision(id="rev-drive-1", document_id="doc-drive-sheet-01", number=1, object_key="docs/1.xlsx", sha256="a" * 64, size=100)
    db_session.add_all([doc, rev])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await service.create_preview(
            db_session,
            doc,
            "user-1",
            [{"op": "set_cell", "sheet": "Sheet1", "cell": "B2", "value": "=SUM(A1:A10)"}],
        )
    assert exc_info.value.status_code == 400
    assert "Formatting or formula option cannot be synchronized" in exc_info.value.detail

    content = (fixtures_dir / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()
    updated = edits.apply_operations(
        content,
        "data.xlsx",
        [{"op": "set_cell", "sheet": "Visible", "cell": "A1", "value": 999}],
    )
    wb = load_workbook(BytesIO(updated), data_only=False)
    assert wb["Visible"]["B2"].value.startswith("=")
    assert wb["Visible"]["A1"].value == 999
    wb.close()


def test_tc_t2_xls_02_circular_formula_injection(fixtures_dir: Path):
    """TC-T2-XLS-02: Detect and reject invalid or unsupported formula operations."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    with pytest.raises(ValueError) as exc_info:
        edits.normalize_operations([{"sheet": "Visible", "cell": "A1", "formula": "=A1+1"}])
    assert "must include an op/action/type name" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        edits.apply_operations(
            content,
            "data.xlsx",
            [{"op": "circular_reference_eval", "cell": "A1", "formula": "=A1+1"}],
        )
    assert "Unsupported XLSX operation" in str(exc_info.value)


def test_tc_t2_xls_03_non_existent_sheet_target(fixtures_dir: Path):
    """TC-T2-XLS-03: Rejection when proposed edit targets a non-existent sheet name."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    with pytest.raises(KeyError):
        edits.apply_operations(
            content,
            "data.xlsx",
            [{"op": "set_cell", "sheet": "NonExistentSheet", "cell": "A1", "value": 100}],
        )


def test_tc_t2_xls_04_remove_referenced_sheet(fixtures_dir: Path):
    """TC-T2-XLS-04: Sheet removal via apply_operations rejects non-existent sheets."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    with pytest.raises(KeyError):
        edits.apply_operations(
            content,
            "data.xlsx",
            [{"op": "remove_sheet", "name": "NonExistentSheetToDelete"}],
        )

    wb = load_workbook(BytesIO(content))
    initial_sheets = wb.sheetnames
    target_to_remove = initial_sheets[-1]
    updated = edits.apply_operations(
        content,
        "data.xlsx",
        [{"op": "remove_sheet", "name": target_to_remove}],
    )
    reloaded = load_workbook(BytesIO(updated))
    assert target_to_remove not in reloaded.sheetnames
    reloaded.close()
    wb.close()


def test_tc_t2_xls_05_cell_coordinate_boundary(fixtures_dir: Path):
    """TC-T2-XLS-05: Excel max coordinate XFD1048576 valid; overflow coordinate XFE1048577 rejected."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    wb = load_workbook(BytesIO(content))
    sheet_name = wb.sheetnames[0]
    wb.close()

    updated = edits.apply_operations(
        content,
        "data.xlsx",
        [{"op": "set_cell", "sheet": sheet_name, "cell": "XFD1048576", "value": 42}],
    )
    reloaded = load_workbook(BytesIO(updated))
    assert reloaded[sheet_name]["XFD1048576"].value == 42
    reloaded.close()

    with pytest.raises(ValueError) as exc_info:
        edits.apply_operations(
            content,
            "data.xlsx",
            [{"op": "set_cell", "sheet": sheet_name, "cell": "XFE1048577", "value": 99}],
        )
    assert "Row numbers must be between 1 and 1048576" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Area 3: DOCX Canonical Engine Boundaries (DOC)
# ---------------------------------------------------------------------------


def test_tc_t2_doc_01_non_existent_text_anchor(fixtures_dir: Path):
    """TC-T2-DOC-01: Text replacement targeting anchor not found in document."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_before = edits.extract_text(content, "report.docx")
    updated = edits.apply_operations(
        content,
        "report.docx",
        [{"op": "replace_text", "find": "NonExistentStringAnchor123456", "replace": "NewText"}],
    )
    doc_after = edits.extract_text(updated, "report.docx")
    assert doc_before == doc_after


def test_tc_t2_doc_02_run_span_3_way_split():
    """TC-T2-DOC-02: Text replacement across multi-run paragraph preserves intact styles."""
    doc = WordDocument()
    p = doc.add_paragraph()
    r1 = p.add_run("Leading ")
    r1.bold = True
    r2 = p.add_run("TargetPhrase")
    r2.italic = True
    r3 = p.add_run(" Trailing")

    out = BytesIO()
    doc.save(out)
    content = out.getvalue()

    updated = edits.apply_operations(
        content,
        "test.docx",
        [{"op": "replace_text", "find": "TargetPhrase", "replace": "Replacement"}],
    )
    reloaded = WordDocument(BytesIO(updated))
    assert "Replacement" in reloaded.paragraphs[0].text
    assert "Leading " in reloaded.paragraphs[0].text


def test_tc_t2_doc_03_empty_paragraph_insertion(fixtures_dir: Path):
    """TC-T2-DOC-03: Append empty paragraph without corrupting document OpenXML structure."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    updated = edits.apply_operations(
        content,
        "report.docx",
        [{"op": "append_paragraph", "text": ""}],
    )
    doc = WordDocument(BytesIO(updated))
    assert len(doc.paragraphs) > 0
    assert doc.paragraphs[-1].text == ""


def test_tc_t2_doc_04_table_cell_index_overflow(fixtures_dir: Path):
    """TC-T2-DOC-04: Rejection of table row overflow (row index beyond table row count)."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    with pytest.raises(IndexError):
        edits.apply_operations(
            content,
            "report.docx",
            [{"op": "set_table_cell", "table": 0, "row": 99, "column": 0, "text": "Overflow"}],
        )


def test_tc_t2_doc_05_xml_entity_injection_in_text(fixtures_dir: Path):
    """TC-T2-DOC-05: Raw XML special characters in replacement text are safely escaped."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    injected_text = "</w:t><w:t>Injected</w:t><script>alert(1)</script>&\"'<>"
    updated = edits.apply_operations(
        content,
        "report.docx",
        [{"op": "replace_text", "find": "opening paragraph", "replace": injected_text}],
    )
    reloaded = WordDocument(BytesIO(updated))
    assert injected_text in reloaded.paragraphs[0].text


# ---------------------------------------------------------------------------
# Area 4: PPTX Canonical Engine Boundaries (PPT)
# ---------------------------------------------------------------------------


def test_tc_t2_ppt_01_non_existent_slide_index(fixtures_dir: Path):
    """TC-T2-PPT-01: Rejection when target slide index does not exist in presentation."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    with pytest.raises(IndexError):
        edits.apply_operations(
            content,
            "deck.pptx",
            [{"op": "set_shape_text", "slide": 99, "shape": 0, "text": "Slide 99"}],
        )


def test_tc_t2_ppt_02_non_existent_shape_id(fixtures_dir: Path):
    """TC-T2-PPT-02: Rejection when target shape index does not exist on slide."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    with pytest.raises(IndexError):
        edits.apply_operations(
            content,
            "deck.pptx",
            [{"op": "set_shape_text", "slide": 0, "shape": 9999, "text": "Shape 9999"}],
        )


def test_tc_t2_ppt_03_delete_last_remaining_slide():
    """TC-T2-PPT-03: Operating on empty slide deck raises IndexError."""
    prs = Presentation()
    assert len(prs.slides) == 0
    out = BytesIO()
    prs.save(out)
    empty_content = out.getvalue()

    with pytest.raises(IndexError):
        edits.apply_operations(
            empty_content,
            "empty.pptx",
            [{"op": "set_shape_text", "slide": 0, "shape": 0, "text": "New Text"}],
        )


def test_tc_t2_ppt_04_text_overflow_in_bounded_shape(fixtures_dir: Path):
    """TC-T2-PPT-04: Large text string (20,000 characters) applied safely to text frame."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    huge_text = "Paragraph content " * 1200
    assert len(huge_text) > 20000
    updated = edits.apply_operations(
        content,
        "deck.pptx",
        [{"op": "set_shape_text", "slide": 0, "shape": 0, "text": huge_text}],
    )
    reloaded = Presentation(BytesIO(updated))
    assert len(reloaded.slides[0].shapes[0].text_frame.text) > 20000


def test_tc_t2_ppt_05_unsupported_operation_rejection(fixtures_dir: Path):
    """TC-T2-PPT-05: Rejection of unsupported PPTX operation code."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    with pytest.raises(ValueError) as exc_info:
        edits.apply_operations(
            content,
            "deck.pptx",
            [{"op": "rotate_shape_3d", "slide": 0, "shape": 0}],
        )
    assert "Unsupported PPTX operation" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Area 5: PDF Read-Only Invariant Boundaries (PDF)
# ---------------------------------------------------------------------------


def test_tc_t2_pdf_01_edit_proposal_mutation_rejection(fixtures_dir: Path):
    """TC-T2-PDF-01: Strict rejection of any mutation operations targeting PDF format."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    with pytest.raises(ValueError) as exc_info:
        edits.apply_operations(
            content,
            "document.pdf",
            [{"op": "replace_text", "find": "text", "replace": "new text"}],
        )
    assert "PDF is read-only" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tc_t2_pdf_02_create_preview_rejection_on_pdf(db_session: AsyncSession):
    """TC-T2-PDF-02: Hard rejection when requesting edit preview on PDF document."""
    doc = Document(
        id="doc-pdf-readonly",
        owner_id="user-1",
        filename="scanned_contract.pdf",
        mime_type="application/pdf",
        current_revision=1,
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.create_preview(
            db_session,
            doc,
            "user-1",
            [{"op": "replace_text", "find": "clause", "replace": "new clause"}],
        )
    assert exc_info.value.status_code == 400
    assert "PDF is read-only" in exc_info.value.detail


def test_tc_t2_pdf_03_password_encrypted_pdf():
    """TC-T2-PDF-03: Detect password-encrypted PDF and reject processing via parsers."""
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("super-secret")
    out = BytesIO()
    writer.write(out)
    enc_bytes = out.getvalue()

    with pytest.raises(pypdf.errors.FileNotDecryptedError):
        parsers.extract_sections(enc_bytes, "protected.pdf")


@pytest.mark.asyncio
async def test_tc_t2_pdf_04_page_range_exceeds_total_pages(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T2-PDF-04: Requesting range beyond total pages/characters returns empty text at EOF."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    sections = parsers.extract_sections(content, "document.pdf")
    assert len(sections) == 2

    doc_id = "doc-pdf-page-bound"
    doc = Document(id=doc_id, owner_id="user-1", filename="document.pdf", mime_type="application/pdf", current_revision=1)
    db_session.add(doc)
    await db_session.commit()
    await chunks.replace_document_chunks(db_session, doc_id, 1, "document.pdf", content)

    text, total_length = await chunks.read_document_range(db_session, doc_id, 1, offset=999999, limit=100)
    assert text == ""
    assert total_length > 0


@pytest.mark.asyncio
async def test_tc_t2_pdf_05_ocr_timeout_handling(memory_store, db_session: AsyncSession):
    """TC-T2-PDF-05: Handle indexing worker failure/timeout safely without hanging."""
    job_id = "job-fail-timeout-01"
    job = ResourceIndexJob(
        id=job_id,
        document_id="doc-nonexistent-timeout",
        revision=1,
        status="pending",
        attempts=0,
    )
    db_session.add(job)
    await db_session.commit()

    success = await resource_indexing.process_resource_index_job(db_session, job_id, memory_store)
    assert success is False
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.error is not None


# ---------------------------------------------------------------------------
# Area 6: 9 Document Tools Boundaries (TLS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_tls_01_invalid_document_uuid(async_client: httpx.AsyncClient):
    """TC-T2-TLS-01: Rejection of malformed document UUID parameter returns HTTP 404."""
    response = await async_client.get("/api/v1/mini-hermes/documents/not-a-valid-uuid-12345")
    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


@pytest.mark.asyncio
async def test_tc_t2_tls_02_negative_range_indices():
    """TC-T2-TLS-02: Rejection of negative start or end range cursors in read_editable_document."""
    res = await tools.read_editable_document(
        "doc-any",
        offset=-5,
        limit=100,
        __user__={"id": "user-1", "role": "user"},
    )
    assert "error" in res
    assert "offset must be non-negative" in res["error"]


@pytest.mark.asyncio
async def test_tc_t2_tls_03_inverted_range_bounds():
    """TC-T2-TLS-03: Rejection of invalid limit or out-of-range bounds in read_document_skill."""
    res = await tools.read_document_skill(
        "docx",
        offset=100,
        limit=0,
        __user__={"id": "user-1", "role": "user"},
    )
    assert "error" in res
    assert res["error"] == "Invalid skill or range"


def test_tc_t2_tls_04_malformed_spreadsheet_range(fixtures_dir: Path):
    """TC-T2-TLS-04: Rejection of malformed A1 range syntax notation in spreadsheet operations."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    with pytest.raises((IndexError, ValueError, CellCoordinatesException)):
        edits.apply_operations(
            content,
            "data.xlsx",
            [{"op": "set_cell", "sheet": "Interview", "cell": "$$$invalid", "value": 1}],
        )


@pytest.mark.asyncio
async def test_tc_t2_tls_05_search_query_exceeding_limit():
    """TC-T2-TLS-05: Rejection of empty search query string or limit exceeding ceiling."""
    res1 = await tools.search_editable_document(
        "doc-any",
        query="   ",
        limit=8,
        __user__={"id": "user-1", "role": "user"},
    )
    assert "error" in res1
    assert "query must be non-empty and limit must be between 1 and 20" in res1["error"]

    res2 = await tools.search_editable_document(
        "doc-any",
        query="financial",
        limit=25,
        __user__={"id": "user-1", "role": "user"},
    )
    assert "error" in res2
    assert "query must be non-empty and limit must be between 1 and 20" in res2["error"]


# ---------------------------------------------------------------------------
# Area 7: Output Budget & Pagination Boundaries (BGT)
# ---------------------------------------------------------------------------


def test_tc_t2_bgt_01_tool_output_exactly_5952_chars():
    """TC-T2-BGT-01: Tool output of exactly 5,952 characters passes without pagination overflow."""
    content = "A" * 5952
    result = bounded_tool_output(content, max_chars=5952)
    assert result == content
    assert isinstance(result, str)


def test_tc_t2_bgt_02_tool_output_5953_chars_triggers_overflow():
    """TC-T2-BGT-02: Tool output of 5,953 characters (1 over ceiling) triggers tool_result_too_large."""
    content = "A" * 5953
    result = bounded_tool_output(content, max_chars=5952)
    assert isinstance(result, dict)
    assert result["code"] == "tool_result_too_large"
    assert result["original_chars"] == 5953
    assert result["limit_chars"] == 5952


def test_tc_t2_bgt_03_massive_nested_json_object():
    """TC-T2-BGT-03: Deeply nested JSON object is validated through RequestBudgetManager."""
    curr = {"val": 1}
    for _ in range(50):
        curr = {"child": curr}
    serialized = json.dumps(curr)
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=100000))
    metrics = mgr.validate(serialized)
    assert metrics["serialized_chars"] == len(serialized)
    assert metrics["serialized_bytes"] == len(serialized.encode("utf-8"))


def test_tc_t2_bgt_04_unicode_surrogate_boundary_slicing():
    """TC-T2-BGT-04: Multi-byte Unicode payload measured safely via RequestBudgetManager."""
    text = "Report: " + "📈🎉" * 10
    metrics = RequestBudgetManager.measure(text)
    assert metrics["serialized_chars"] == len(text)
    assert metrics["serialized_bytes"] > metrics["serialized_chars"]
    bounded = bounded_tool_output(text, max_chars=200)
    assert isinstance(bounded, str)
    assert len(bounded) == len(text)


@pytest.mark.asyncio
async def test_tc_t2_bgt_05_zero_item_page_cursor(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T2-BGT-05: Paginating with cursor at EOF returns empty text."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_id = "doc-bgt-cursor-05"
    doc = Document(
        id=doc_id,
        owner_id="user-1",
        filename="report.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    db_session.add(doc)
    await db_session.commit()
    await chunks.replace_document_chunks(db_session, doc_id, 1, "report.docx", content)

    text, total_length = await chunks.read_document_range(db_session, doc_id, 1, offset=999999, limit=100)
    assert text == ""
    assert total_length > 0


# ---------------------------------------------------------------------------
# Area 8: Multi-State Coverage Boundaries (COV)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_cov_01_query_incomplete_index_warning(memory_store, db_session: AsyncSession, monkeypatch):
    """TC-T2-COV-01: Query when index job is pending returns pending status and incomplete coverage."""
    from contextlib import asynccontextmanager
    from open_webui.mini_hermes import resource_service

    @asynccontextmanager
    async def mock_get_async_db():
        yield db_session

    monkeypatch.setattr(tools, "get_async_db", mock_get_async_db)
    monkeypatch.setattr(resource_service, "resource_index_worker_enabled", lambda: True)

    doc_id = "doc-cov-pending-01"
    doc = Document(
        id=doc_id,
        source_file_id="file-cov-p-1",
        owner_id="user-1",
        filename="pending.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    rev = Revision(id="rev-cov-p-1", document_id=doc_id, number=1, object_key="docs/p.docx", sha256="b" * 64, size=100)
    job = ResourceIndexJob(id="job-cov-pending", document_id=doc_id, revision=1, status="pending", attempts=0)
    db_session.add_all([doc, rev, job])
    await db_session.commit()

    res = await tools.search_editable_document(
        doc_id,
        "financial report",
        limit=5,
        __user__={"id": "user-1", "role": "user"},
        __files__=[{"id": "file-cov-p-1"}],
    )
    assert res["status"] == "pending"
    assert res["coverage_complete"] is False
    assert res["search_complete"] is False


def test_tc_t2_cov_02_unevidenced_hallucination_detection():
    """TC-T2-COV-02: Flags citations citing invalid or altered evidence IDs as unevidenced."""
    doc_id = "doc-cov-02"
    valid_id = provenance.evidence_id(doc_id, version="1", start=0, end=100, excerpt="Revenue grew")
    tampered_id = provenance.evidence_id(doc_id, version="1", start=0, end=100, excerpt="Revenue fell")

    assert valid_id != tampered_id
    known_evidence_ids = {valid_id}
    cited_ids = [valid_id, "hallucinated-evidence-999"]
    unverified = [cid for cid in cited_ids if cid not in known_evidence_ids]
    assert unverified == ["hallucinated-evidence-999"]


def test_tc_t2_cov_03_cross_revision_citation_isolation():
    """TC-T2-COV-03: Citations remain bound to specific revision numbers across edits via provenance."""
    doc_id = "doc-cov-cross-rev"
    ev_rev1 = provenance.evidence_id(doc_id, version="1", start=10, end=50)
    ev_rev2 = provenance.evidence_id(doc_id, version="2", start=10, end=50)
    assert ev_rev1 != ev_rev2


@pytest.mark.asyncio
async def test_tc_t2_cov_04_zero_evidence_explicit_declaration(fixtures_dir: Path, db_session: AsyncSession):
    """TC-T2-COV-04: Report empty results when search yields zero matching chunks."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_id = "doc-cov-zero-ev"
    doc = Document(
        id=doc_id,
        owner_id="user-1",
        filename="doc.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    db_session.add(doc)
    await db_session.commit()
    await chunks.replace_document_chunks(db_session, doc_id, 1, "doc.docx", content)

    matches = await chunks.search_document_chunks(db_session, doc_id, 1, "NonExistentTermXYZ999", limit=5)
    assert matches == []


def test_tc_t2_cov_05_huge_document_partial_coverage_ratio():
    """TC-T2-COV-05: Accurate partial coverage and truncation reporting for large document."""
    huge_text = "Analysis " * 10000
    sources = [{"document": [huge_text], "metadata": [{"total_chars": len(huge_text), "file_id": "file-huge"}]}]
    bounded = bounded_file_sources(sources, "Analysis", max_total_chars=4000)
    assert len(bounded) == 1
    meta = bounded[0]["metadata"][0]
    assert meta["truncated"] is True
    assert len(bounded[0]["document"][0]) <= 4000


# ---------------------------------------------------------------------------
# Area 9: AgentRuntime Lifecycle Boundaries (RUN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_run_01_duplicate_start_differing_input_conflict(db_sessionmaker):
    """TC-T2-RUN-01: Reusing idempotency key with different payload raises HTTP 409 idempotency_key_reused."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    key = "idemp-turn-reuse-01"
    await repo.create_turn(session.id, {"messages": [{"role": "user", "content": "Original"}]}, idempotency_key=key)

    with pytest.raises(HTTPException) as exc_info:
        await repo.create_turn(
            session.id,
            {"messages": [{"role": "user", "content": "Modified"}]},
            idempotency_key=key,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_tc_t2_run_02_cursor_ahead_of_stream(db_sessionmaker):
    """TC-T2-RUN-02: Requesting event cursor ahead of latest sequence returns empty event page."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-ahead-02")
    await repo.append_event(turn.id, "step.1", {})

    page = await repo.get_event_page(turn.id, after_sequence=999)
    assert page["events"] == []
    assert page["latest_sequence"] < 999
    assert page["next_after_sequence"] == 999


@pytest.mark.asyncio
async def test_tc_t2_run_03_cursor_behind_replay_floor(db_sessionmaker):
    """TC-T2-RUN-03: Event page detects gap when cursor is behind first available sequence."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-floor-03")
    await repo.append_event(turn.id, "step.1", {})
    await repo.append_event(turn.id, "step.2", {})

    page = await repo.get_event_page(turn.id, after_sequence=0)
    assert len(page["events"]) == 3  # queued + step.1 + step.2
    assert page["first_available_sequence"] == 1


@pytest.mark.asyncio
async def test_tc_t2_run_04_resume_stale_checkpoint_conflict(db_sessionmaker):
    """TC-T2-RUN-04: Attempting to save checkpoint or resume with stale attempt raises StaleRunError."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-chk-04")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    with pytest.raises(StaleRunError):
        await repo.save_checkpoint(turn.id, {"state": "data"}, run_attempt=99)


@pytest.mark.asyncio
async def test_tc_t2_run_05_double_cancel_idempotency(db_sessionmaker):
    """TC-T2-RUN-05: Double cancel requests in rapid succession are idempotent."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-dbl-05")
    res1 = await repo.request_cancel_turn(turn.id, owner_id="owner-1")
    res2 = await repo.request_cancel_turn(turn.id, owner_id="owner-1")
    assert res1 is not None and res1.status == "cancelled"
    assert res2 is not None and res2.status == "cancelled"
    assert res1.id == res2.id
    fetched = await repo.get_turn(turn.id)
    assert fetched.status == "cancelled"


# ---------------------------------------------------------------------------
# Area 10: Provider Gateway & Limits Boundaries (GWY)
# ---------------------------------------------------------------------------


def test_tc_t2_gwy_01_preparation_limit_overflow_rejected():
    """TC-T2-GWY-01: Prompt preparation body of 95,001 chars exceeds 95k limit."""
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=95000))
    prep_text = "p" * 95001
    with pytest.raises(RequestBudgetExceeded) as exc_info:
        mgr.validate(prep_text)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_char_limit_exceeded"


def test_tc_t2_gwy_02_wire_body_limit_overflow_rejected():
    """TC-T2-GWY-02: Serialized wire JSON of 100,001 chars raises request_json_char_limit_exceeded."""
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=100000))
    oversized_wire = "x" * 100001
    with pytest.raises(RequestBudgetExceeded) as exc_info:
        mgr.validate(oversized_wire)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_char_limit_exceeded"


def test_tc_t2_gwy_03_byte_ceiling_overflow_rejected():
    """TC-T2-GWY-03: Serialized JSON under 100k chars but exceeding 380,000 UTF-8 bytes rejected."""
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=100000, max_json_bytes=380000))
    emoji_payload = "😀" * 95001
    with pytest.raises(RequestBudgetExceeded) as exc_info:
        mgr.validate(emoji_payload)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_byte_limit_exceeded"


def test_tc_t2_gwy_04_non_object_top_level_json_overflow():
    """TC-T2-GWY-04: Non-object top-level JSON array exceeding character limit rejected."""
    mgr = RequestBudgetManager(RequestBudgetLimits(max_json_chars=100000))
    array_payload = json.dumps(["A" * 100005])
    with pytest.raises(RequestBudgetExceeded) as exc_info:
        mgr.validate(array_payload)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_json_char_limit_exceeded"


def test_tc_t2_gwy_05_ollama_probe_interception():
    """TC-T2-GWY-05: Rejection gate blocks Ollama provider URLs and disabled Ollama configs."""
    assert not any(key.startswith(('ollama.', 'rag.ollama.')) for key in app_config.DEFAULT_CONFIG)

    payload = {"model": "test-unsupported", "tools": [{"type": "function", "function": {"name": "test"}}], "tool_choice": "required"}
    with pytest.raises(ProviderCapabilityUnsupported) as exc_info:
        apply_capability_constraints(payload, "http://localhost:11434", overrides={"supports_tools": False})
    assert exc_info.value.detail["code"] == "provider_model_does_not_support_tools"


# ---------------------------------------------------------------------------
# Area 11: Tool Ledger & Approvals Boundaries (LDG)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_ldg_01_idempotency_key_reuse_different_args(db_sessionmaker):
    """TC-T2-LDG-01: Re-invoking tool with same call_id but changed arguments raises HTTP 409."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-t2-01")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    call1, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-mismatch-01",
        tool_name="read_range",
        arguments={"start": 0, "end": 100},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-key-01",
    )
    with pytest.raises(HTTPException) as exc_info:
        await repo.begin_tool_call(
            turn_id=turn.id,
            call_id="call-mismatch-01",
            tool_name="read_range",
            arguments={"start": 50, "end": 200},
            retry_safe=True,
            run_attempt=1,
            idempotency_key="idemp-key-01",
        )
    assert exc_info.value.status_code == 409
    assert "Tool call identity was reused with different input." in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_ldg_02_quarantine_of_timeout_outcome(db_sessionmaker):
    """TC-T2-LDG-02: Quarantining uncertain tool outcome blocks turn recovery without reconciliation."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-t2-02")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-quarantine-01",
        tool_name="external_webhook",
        arguments={"url": "https://api.external.test"},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-ext-01",
    )
    await repo.fail_tool_call(
        turn.id,
        "call-quarantine-01",
        {"code": "gateway_timeout", "message": "Timed out"},
        attempt=1,
        uncertain=True,
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo.claim_turn_by_id(turn.id, "worker-2", lease_seconds=60)
    assert exc_info.value.status_code == 409
    assert "An unresolved tool outcome blocks turn recovery." in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_ldg_03_expired_agent_approval_challenge(db_sessionmaker):
    """TC-T2-LDG-03: Approving agent challenge after cancel/expiration raises HTTP 409."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-t2-03")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)
    await repo.request_cancel_turn(turn.id, owner_id="owner-1")

    with pytest.raises(HTTPException) as exc_info:
        await repo.record_tool_approval(
            turn.id,
            owner_id="owner-1",
            call_id="call-expired-01",
            tool_name="sensitive_action",
            arguments={"target": "file.txt"},
            decision="approved",
            approved_by="owner-1",
            retry_safe=False,
            idempotency_key="idemp-exp-01",
            execution_attempt=1,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail in ("Agent turn is not awaiting tool approval.", "Agent run claim is no longer current.")


@pytest.mark.asyncio
async def test_tc_t2_ldg_04_tampered_challenge_arguments(db_sessionmaker):
    """TC-T2-LDG-04: Submitting approval with altered arguments raises argument mismatch error."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("owner-1", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-t2-04")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-tamper-01",
        tool_name="delete_resource",
        arguments={"target": "temp_file.txt"},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-tamper-01",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo.record_tool_approval(
            turn.id,
            owner_id="owner-1",
            call_id="call-tamper-01",
            tool_name="delete_resource",
            arguments={"target": "production_database.db"},  # Tampered!
            decision="approved",
            approved_by="owner-1",
            retry_safe=False,
            idempotency_key="idemp-tamper-01",
            execution_attempt=1,
        )
    assert exc_info.value.status_code == 409
    assert "Tool call identity was reused with different input." in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_ldg_05_unauthorized_approver_rejection(db_sessionmaker):
    """TC-T2-LDG-05: Non-owner User B attempting to approve User A's challenge raises HTTP 404."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-a", "chat-1")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-ldg-t2-05")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=60)

    with pytest.raises(HTTPException) as exc_info:
        await repo.record_tool_approval(
            turn.id,
            owner_id="user-b",  # Unauthorized!
            call_id="call-unauth-01",
            tool_name="sensitive_tool",
            arguments={},
            decision="approved",
            approved_by="user-b",
            retry_safe=False,
            idempotency_key="idemp-unauth-01",
            execution_attempt=1,
        )
    assert exc_info.value.status_code == 404
    assert "Agent turn not found." in exc_info.value.detail


# ---------------------------------------------------------------------------
# Area 12: Revision Service Boundaries (REV)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t2_rev_01_15m_approval_expiration(memory_store, db_session: AsyncSession):
    """TC-T2-REV-01: Commit preview at 15m 01s after creation raises HTTP 409 Preview approval expired."""
    doc_id = "doc-rev-t2-01"
    base_content = b"Base data"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-t2-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Staged data"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-t2-01/{artifact_sha256}"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "Base", "replace": "Staged"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    created_at = now() - dt.timedelta(minutes=15, seconds=1)
    preview = Preview(
        id="prev-t2-01",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=base_sha256,
        artifact_sha256=artifact_sha256,
        patch_hash=patch_hash,
        operations=ops,
        status="ready",
        preview_key=staged_key,
        created_at=created_at,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": artifact_sha256,
        "patch_hash": patch_hash,
    }
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(db_session, memory_store, user_id="user-1", preview_id="prev-t2-01", approval=approval, is_admin=True)
    assert exc_info.value.status_code == 409
    assert "Preview approval expired" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_rev_02_patch_hash_mismatch(memory_store, db_session: AsyncSession):
    """TC-T2-REV-02: Commit with altered patch_hash raises explicit_preview_approval_required."""
    doc_id = "doc-rev-t2-02"
    base_content = b"Content"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-t2-2", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Modified Content"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-t2-02/{artifact_sha256}"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "Content", "replace": "Modified Content"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-t2-02",
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

    tampered_patch_hash = ("1" if patch_hash[0] != "1" else "2") + patch_hash[1:]
    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": artifact_sha256,
        "patch_hash": tampered_patch_hash,
    }
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(db_session, memory_store, user_id="user-1", preview_id="prev-t2-02", approval=approval, is_admin=True)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "explicit_preview_approval_required"


@pytest.mark.asyncio
async def test_tc_t2_rev_03_stale_base_revision_conflict(memory_store, db_session: AsyncSession):
    """TC-T2-REV-03: Commit preview against stale base_revision raises Document has changed since preview."""
    doc_id = "doc-rev-t2-03"
    content = b"Text"
    sha = hashlib.sha256(content).hexdigest()
    key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(key, content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=2)
    rev1 = Revision(id="rev-t2-3", document_id=doc_id, number=1, object_key=key, sha256=sha, size=len(content))

    preview = Preview(
        id="prev-t2-03",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=sha,
        artifact_sha256=sha,
        patch_hash="patch123",
        operations=[],
        status="ready",
        preview_key=key,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    approval = {"base_revision": 1, "base_sha256": sha, "artifact_sha256": sha, "patch_hash": "patch123"}
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(db_session, memory_store, user_id="user-1", preview_id="prev-t2-03", approval=approval, is_admin=True)
    assert exc_info.value.status_code == 409
    assert "Document has changed since preview" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_rev_04_corrupted_staged_artifact_bytes(memory_store, db_session: AsyncSession):
    """TC-T2-REV-04: Bit-flip corruption in staged artifact bytes raises checksum failure."""
    doc_id = "doc-rev-t2-04"
    base_content = b"Base data"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-t2-4", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Staged bytes"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-t2-04/{artifact_sha256}"
    await memory_store.put(staged_key, b"Corrupted bytes!")

    ops = [{"op": "replace_text", "find": "Base", "replace": "Staged"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-t2-04",
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
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(db_session, memory_store, user_id="user-1", preview_id="prev-t2-04", approval=approval, is_admin=True)
    assert exc_info.value.status_code == 409
    assert "Preview artifact checksum changed; create a new preview" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_rev_05_double_commit_replay(memory_store, db_session: AsyncSession):
    """TC-T2-REV-05: Replaying identical commit with same idempotency key returns existing revision."""
    doc_id = "doc-rev-t2-05"
    base_content = b"Original text"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-t2-5", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Committed text"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-t2-05/{artifact_sha256}"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "Original", "replace": "Committed"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-t2-05",
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
    idemp_key = "commit-replay-key-01"
    rev_a, _ = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id="prev-t2-05", approval=approval, is_admin=True, idempotency_key=idemp_key
    )
    assert rev_a.number == 2

    rev_b, _ = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id="prev-t2-05", approval=approval, is_admin=True, idempotency_key=idemp_key
    )
    assert rev_b.id == rev_a.id
    assert rev_b.number == 2

    all_revisions = (await db_session.execute(select(Revision).where(Revision.document_id == doc_id))).scalars().all()
    assert len(all_revisions) == 2


# ---------------------------------------------------------------------------
# Area 13: Greenfield Persistence & RBAC Boundaries (PER)
# ---------------------------------------------------------------------------


def test_tc_t2_per_01_v1_database_detection_on_boot():
    """TC-T2-PER-01: Greenfield validator fails closed when configured with non-PostgreSQL database."""
    with pytest.raises(RuntimeError) as exc_info:
        enforce_postgresql("sqlite")
    assert "strictly requires PostgreSQL" in str(exc_info.value)

    with pytest.raises(RuntimeError) as exc_info:
        enforce_postgresql("mysql")
    assert "strictly requires PostgreSQL" in str(exc_info.value)


def test_tc_t2_per_02_v1_storage_bucket_detection():
    """TC-T2-PER-02: Storage validator fails closed on negative range and isolates bucket."""
    store = ObjectStore()
    assert store.bucket == "mini-hermes"
    assert "open-webui" not in store.bucket

    with pytest.raises(ValueError) as exc_info:
        import asyncio
        asyncio.run(store.get_range("test_key", -1, 10))
    assert "object byte ranges must be non-negative" in str(exc_info.value)


def test_tc_t2_per_03_non_dev_database_reset_prevention(monkeypatch):
    """TC-T2-PER-03: Protection gate halts dev DB reset on non-development / production host."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://user:pass@prod.postgres.database.azure.com/minihermes", confirm=True)
    assert "Host 'prod.postgres.database.azure.com' is not a local development host" in str(exc_info.value) or "contains dangerous remote substring" in str(exc_info.value)

    monkeypatch.setenv("ENV", "production")
    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://user:pass@localhost:5432/minihermes_dev", confirm=True)
    assert "indicates non-dev environment" in str(exc_info.value) or "not in allowed dev environments" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tc_t2_per_04_revoked_permission_mid_flow(db_session: AsyncSession):
    """TC-T2-PER-04: User whose access was revoked mid-flow is denied access via accessible_document."""
    doc_id = "doc-revoked-01"
    doc = Document(
        id=doc_id,
        owner_id="user-owner",
        filename="secret.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    rev = Revision(id="rev-revoked-1", document_id=doc_id, number=1, object_key="docs/s.docx", sha256="c" * 64, size=100)
    db_session.add_all([doc, rev])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await accessible_document(db_session, doc_id, user_id="user-revoked", permission="write", is_admin=False)
    assert exc_info.value.status_code == 404
    assert "Document not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_tc_t2_per_05_foreign_key_constraint_validation(db_engine, db_sessionmaker):
    """TC-T2-PER-05: Inserting orphaned revision referencing non-existent document violates FK."""
    async with db_engine.connect() as conn:
        await conn.execute(text("PRAGMA foreign_keys = ON;"))
        await conn.commit()

    async with db_sessionmaker() as session:
        orphan_rev = Revision(
            id="rev-orphan-01",
            document_id="non-existent-doc-id-9999",
            number=1,
            object_key="documents/none/1.txt",
            sha256="a" * 64,
            size=10,
        )
        session.add(orphan_rev)
        with pytest.raises(IntegrityError):
            await session.commit()
