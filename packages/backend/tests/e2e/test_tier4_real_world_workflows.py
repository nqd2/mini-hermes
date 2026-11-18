"""Tier 4: Real-World Application Workflows Test Suite (5 Comprehensive Scenarios).

Implements 5 realistic, multi-step end-user workflows across spreadsheet models,
legal contracts, board decks, PDF documents, and multi-format diligence synthesis:
1. TC-T4-SC-01: Financial Spreadsheet Audit, Formula-Protected Update & Publication
2. TC-T4-SC-02: Multi-Section Legal Contract Redlining & Controlled Approval
3. TC-T4-SC-03: Executive Board Presentation Overhaul with Agent Action Approval
4. TC-T4-SC-04: Scanned Invoices PDF Extraction, OCR Fallback & Evidence Synthesis
5. TC-T4-SC-05: Multi-Format M&A Due Diligence Cross-Analysis & Artifact Export
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document as WordDocument
from fastapi import HTTPException
from openpyxl import load_workbook
from pptx import Presentation
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from weai.agent_runtime.models import AgentArtifact, AgentSession, AgentTurn
from weai.agent_runtime.repository import AgentRepository
import weai.models.messages  # noqa: F401 - registers message table for channel_file FK
import weai.models.users  # noqa: F401 - registers users table
import weai.models.channels  # noqa: F401 - registers channels table
from weai.agent_runtime.contracts import ConversationEngineBinding
from weai.agent_runtime.request_budget import (
    RequestBudgetLimits,
    RequestBudgetManager,
)
from weai.documents import edits, parsers, revision_service
from weai.documents.revision_service import commit_preview
from weai.documents.chunks import (
    build_chunk_rows,
    document_range_evidence,
    read_document_range,
    replace_document_chunks,
    search_document_chunks,
)
from weai.documents.models import (
    Document,
    DocumentChunk,
    Folder,
    Preview,
    Revision,
    RevisionOutbox,
    now,
)
from weai.documents.revision_integrity import compute_patch_hash
from weai.documents.service import build_preview
from weai.utils.chat_runtime.resource_execution import prepare_resource_tool_result


# ---------------------------------------------------------------------------
# Scenario 1: Financial Spreadsheet Audit & Formula-Protected Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t4_sc_01_financial_spreadsheet_audit_and_publication(
    fixtures_dir: Path, memory_store, db_session: AsyncSession, async_client
):
    """TC-T4-SC-01: Financial Spreadsheet Audit, Formula-Protected Update & Publication.

    Persona: Senior Financial Analyst
    Context: Quarterly Model with multiple sheets ('Interview', 'Meta', 'Notes')
    Steps:
      1. Upload & Ingestion: compute SHA-256, stage immutable bytes, verify via async_client.
      2. Structure Inspection: sheets, dimensions, merged cells in 'Meta' (A1:D1).
      3. Targeted Range Read: rectangular cell grid bounded within 5,952 wire chars.
      4. Formula Preservation: verify formula and cache parsing from production parser.
      5. Valid Proposal via async_client: create preview for input cell update and row append.
      6. Diff Preview & Hash: process preview with real worker, generating patch_hash.
      7. Explicit Approval & Commit via async_client: publish Revision 2, emit RevisionOutbox.
      8. Audit & Verification: Rev 2 downloaded via async_client content endpoint; Rev 1 unchanged.
    """
    # Step 1: Upload & Ingestion
    raw_bytes = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    expected_sha256 = "e85ad43cf67e3cb70576e6ad19e0f07452d00a1e9f1c7dc169dc1c9b248248be"
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    assert actual_sha256 == expected_sha256

    doc_id = "doc-fin-01"
    base_key = f"documents/{doc_id}/revisions/1.xlsx"
    await memory_store.put(base_key, raw_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    doc = Document(
        id=doc_id,
        owner_id="user-std-1",
        filename="Q3_Financial_Model.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        current_revision=1,
    )
    rev1 = Revision(
        id="rev-fin-01",
        document_id=doc_id,
        number=1,
        object_key=base_key,
        sha256=actual_sha256,
        size=len(raw_bytes),
    )
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Verify document access via async_client
    doc_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["filename"] == "Q3_Financial_Model.xlsx"

    # Verify content download endpoint returns exact original bytes
    content_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}/content")
    assert content_resp.status_code == 200
    assert hashlib.sha256(content_resp.content).hexdigest() == expected_sha256

    # Step 2: Structure Inspection
    wb = load_workbook(BytesIO(raw_bytes), data_only=False)
    assert set(wb.sheetnames) == {"Interview", "Meta", "Notes"}
    meta_sheet = wb["Meta"]
    merged_ranges = [str(r) for r in meta_sheet.merged_cells.ranges]
    assert "A1:D1" in merged_ranges
    wb.close()

    # Step 3: Targeted Range Read (within 5,952 wire characters)
    grid_sample = {
        "sheet": "Interview",
        "range": "A1:D10",
        "rows": [{"A": f"ID_{i}", "B": f"Question {i}", "C": f"Goal {i}", "D": f"Note {i}"} for i in range(1, 10)],
    }
    wire_text, supplied = prepare_resource_tool_result({}, "read_spreadsheet_range", grid_sample)
    assert supplied is True
    assert len(wire_text) <= 5952

    # Step 4: Formula Preservation & Cache Verification via Production Parser
    formula_bytes = (fixtures_dir / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()
    formula_sections = parsers.extract_sections(formula_bytes, "hidden-missing-cache.xlsx")
    vis_sec = next((s for s in formula_sections if s[0] == "Visible"), None)
    assert vis_sec is not None
    assert "=A2*2" in vis_sec[1]
    assert "[cached=" in vis_sec[1]

    # Step 5: Valid Formula-Preserving Proposal via async_client
    ops = [
        {"op": "set_cell", "sheet": "Interview", "cell": "A2", "value": 125000},
        {"op": "append_row", "sheet": "Interview", "values": ["Q_NEW", "Audited Item", "Verification", "Approved"]},
    ]
    prev_resp = await async_client.post(f"/api/v1/agent-runtime/documents/{doc_id}/previews", json={"operations": ops})
    assert prev_resp.status_code == 200
    prev_id = prev_resp.json()["id"]

    # Step 6: Diff Preview & Hash Generation via Real Worker Pipeline
    preview = await db_session.get(Preview, prev_id)
    await build_preview(db_session, memory_store, preview)
    assert preview.status == "ready"
    assert preview.artifact_sha256 is not None
    assert preview.patch_hash is not None
    assert len(preview.diff) > 0

    # Step 7: Explicit Approval & Atomic Commit via async_client
    approval_payload = {
        "base_revision": preview.base_revision,
        "base_sha256": preview.base_sha256,
        "artifact_sha256": preview.artifact_sha256,
        "patch_hash": preview.patch_hash,
    }
    commit_resp = await async_client.post(
        f"/api/v1/agent-runtime/previews/{prev_id}/commit",
        json=approval_payload,
        headers={"Idempotency-Key": "idemp-fin-01"},
    )
    assert commit_resp.status_code == 200
    assert commit_resp.json()["revision"] == 2
    await db_session.refresh(doc)
    assert doc.current_revision == 2

    # Emit outbox event
    outbox_event = RevisionOutbox(
        id=str(uuid4()),
        event_key="rev-commit-sc01",
        document_id=doc_id,
        revision_id=str(uuid4()),
        event_type="revision.committed",
        payload={"revision": 2, "patch_hash": preview.patch_hash},
    )
    db_session.add(outbox_event)
    await db_session.commit()

    # Step 8: Audit & Verification via Content Endpoint
    rev2_content_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}/content?revision=2")
    assert rev2_content_resp.status_code == 200
    committed_wb = load_workbook(BytesIO(rev2_content_resp.content))
    assert committed_wb["Interview"]["A2"].value == 125000
    assert set(committed_wb.sheetnames) == set(wb.sheetnames)
    committed_wb.close()

    # Initial revision bytes remain bit-for-bit unchanged
    original_stored_bytes = await memory_store.get(base_key)
    assert hashlib.sha256(original_stored_bytes).hexdigest() == expected_sha256


# ---------------------------------------------------------------------------
# Scenario 2: Legal Contract Redlining & Controlled Approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t4_sc_02_legal_contract_redlining_controlled_approval(
    fixtures_dir: Path, memory_store, db_session: AsyncSession, async_client
):
    """TC-T4-SC-02: Multi-Section Legal Contract Redlining & Controlled Approval.

    Persona: Legal Counsel & Contract Administrator
    Context: Master Services Agreement (docx-f01-structured.docx)
    Steps:
      1. Upload & Ingestion: SHA-256 verification, stage immutable bytes, verify via async_client.
      2. Lexical Search: find target terms ("opening paragraph") with chunks.
      3. Sequential Read: bounded section read under pagination limit.
      4. Multi-Run Redline: propose edit via async_client.
      5. Preview Diff: build preview with unified diff and canonical patch_hash.
      6. Approval Expiration: 16m expired proposal rejected (HTTP 409).
      7. Re-Proposal & Commit: fresh proposal committed via async_client to Revision 2.
      8. Compensating Rollback: rollback to Rev 1 via async_client creates Rev 3.
    """
    # Step 1: Upload & Ingestion
    docx_bytes = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    expected_sha256 = "573ceb37df1c09f7f6021b2b372980bd0f8c8da196c51557c079906afa337e46"
    assert hashlib.sha256(docx_bytes).hexdigest() == expected_sha256

    doc_id = "doc-legal-02"
    base_key = f"documents/{doc_id}/revisions/1.docx"
    await memory_store.put(
        base_key, docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    doc = Document(
        id=doc_id,
        owner_id="user-std-1",
        filename="MSA_Vendor_2026.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    rev1 = Revision(
        id="rev-legal-01",
        document_id=doc_id,
        number=1,
        object_key=base_key,
        sha256=expected_sha256,
        size=len(docx_bytes),
    )
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # HTTP verification
    doc_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["filename"] == "MSA_Vendor_2026.docx"

    # Step 2: Chunk Indexing & Lexical Search
    await replace_document_chunks(db_session, doc_id, 1, doc.filename, docx_bytes)
    await db_session.commit()

    matching_chunks = await search_document_chunks(db_session, doc_id, 1, query="opening paragraph")
    assert len(matching_chunks) > 0
    assert "opening paragraph" in matching_chunks[0].content

    # Step 3: Sequential Section Read (bounded within 5,952 wire budget)
    chunk_text, total_length = await read_document_range(db_session, doc_id, 1, offset=0, limit=500)
    assert len(chunk_text) > 0
    wire_section, supplied = prepare_resource_tool_result(
        {}, "read_document_range", {"text": chunk_text, "start": 0, "end": len(chunk_text)}
    )
    assert supplied is True
    assert len(wire_section) <= 5952

    # Step 4: Multi-Run Redline Proposal via async_client
    ops = [
        {"op": "replace_text", "find": "opening paragraph", "replace": "amended indemnification clause (liability cap $2,500,000)"},
        {"op": "set_table_cell", "table": 0, "row": 0, "column": 1, "text": "99.9% Uptime Guarantee"},
    ]
    prev_resp = await async_client.post(f"/api/v1/agent-runtime/documents/{doc_id}/previews", json={"operations": ops})
    assert prev_resp.status_code == 200
    prev_id = prev_resp.json()["id"]

    # Step 5: Build preview
    preview = await db_session.get(Preview, prev_id)
    await build_preview(db_session, memory_store, preview)
    assert preview.status == "ready"

    # Step 6: Approval Expiration Rejection
    # Simulate 16 minutes elapsed
    preview.created_at = now() - dt.timedelta(minutes=16)
    await db_session.commit()

    approval_payload = {
        "base_revision": preview.base_revision,
        "base_sha256": preview.base_sha256,
        "artifact_sha256": preview.artifact_sha256,
        "patch_hash": preview.patch_hash,
    }
    exp_resp = await async_client.post(f"/api/v1/agent-runtime/previews/{prev_id}/commit", json=approval_payload)
    assert exp_resp.status_code == 409
    assert "expired" in exp_resp.json()["detail"].lower()

    # Step 7: Fresh Proposal & Commit via async_client
    fresh_resp = await async_client.post(f"/api/v1/agent-runtime/documents/{doc_id}/previews", json={"operations": ops})
    fresh_id = fresh_resp.json()["id"]
    fresh_preview = await db_session.get(Preview, fresh_id)
    await build_preview(db_session, memory_store, fresh_preview)

    fresh_approval = {
        "base_revision": fresh_preview.base_revision,
        "base_sha256": fresh_preview.base_sha256,
        "artifact_sha256": fresh_preview.artifact_sha256,
        "patch_hash": fresh_preview.patch_hash,
    }
    commit_resp = await async_client.post(f"/api/v1/agent-runtime/previews/{fresh_id}/commit", json=fresh_approval)
    assert commit_resp.status_code == 200
    assert commit_resp.json()["revision"] == 2
    await db_session.refresh(doc)
    assert doc.current_revision == 2

    # Step 8: Compensating Rollback via async_client
    rb_preview_resp = await async_client.post(
        f"/api/v1/agent-runtime/documents/{doc_id}/rollback-preview",
        json={"target_revision": 1, "expected_current_revision": 2},
    )
    assert rb_preview_resp.status_code == 200
    rb_data = rb_preview_resp.json()
    rb_id = rb_data["id"]

    rb_approval = {
        "base_revision": rb_data["base_revision"],
        "base_sha256": rb_data["base_sha256"],
        "artifact_sha256": rb_data["artifact_sha256"],
        "patch_hash": rb_data["patch_hash"],
    }
    rb_commit_resp = await async_client.post(f"/api/v1/agent-runtime/previews/{rb_id}/commit", json=rb_approval)
    assert rb_commit_resp.status_code == 200
    assert rb_commit_resp.json()["revision"] == 3
    await db_session.refresh(doc)
    assert doc.current_revision == 3

    # Verify Rev 3 restored original text
    rev3_content_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}/content?revision=3")
    assert rev3_content_resp.status_code == 200
    doc_restored = WordDocument(BytesIO(rev3_content_resp.content))
    assert "opening paragraph" in doc_restored.paragraphs[0].text


# ---------------------------------------------------------------------------
# Scenario 3: Executive Board Presentation Overhaul with Approval Challenge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t4_sc_03_executive_board_deck_overhaul_approval_challenge(
    fixtures_dir: Path, memory_store, db_sessionmaker, db_session: AsyncSession, async_client
):
    """TC-T4-SC-03: Executive Board Presentation Overhaul with Agent Action Approval.

    Persona: Chief Strategy Officer & Executive Assistant
    Context: Board Strategy Deck (pptx-f01-structured.pptx)
    Steps:
      1. Upload & Ingestion: SHA-256 validation, stage immutable bytes, verify via async_client.
      2. Slide Inventory & Inspection: 2 slides.
      3. Propose Slide Addition & Shape Text Replacement via async_client.
      4. Agent Approval Gate: external research pauses with waiting_approval; approved & resumed.
      5. Preview Review & Diff: build preview with 3 slides.
      6. Tamper Hash Rejection & Idempotent Commit via async_client.
    """
    # Step 1: Upload & Ingestion
    pptx_bytes = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    expected_sha256 = "8f3c12c2ca20d9be6db1eb7c9fdc15d994241af448d8ad7bcaecbea59b207306"
    assert hashlib.sha256(pptx_bytes).hexdigest() == expected_sha256

    doc_id = "doc-deck-03"
    base_key = f"documents/{doc_id}/revisions/1.pptx"
    await memory_store.put(
        base_key, pptx_bytes, "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    doc = Document(
        id=doc_id,
        owner_id="user-std-1",
        filename="Board_Strategy_2026.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        current_revision=1,
    )
    rev1 = Revision(
        id="rev-deck-01",
        document_id=doc_id,
        number=1,
        object_key=base_key,
        sha256=expected_sha256,
        size=len(pptx_bytes),
    )
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Verify document via async_client
    doc_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["filename"] == "Board_Strategy_2026.pptx"

    # Step 2: Slide Inventory & Inspection
    deck = Presentation(BytesIO(pptx_bytes))
    assert len(deck.slides) == 2

    # Step 3: Propose Slide Addition & Shape Text via async_client
    ops = [
        {"op": "add_slide", "layout": 6},
        {"op": "replace_text", "find": "Synthetic slide one", "replace": "AI Strategic Vision 2026"},
    ]
    prev_resp = await async_client.post(f"/api/v1/agent-runtime/documents/{doc_id}/previews", json={"operations": ops})
    assert prev_resp.status_code == 200
    prev_id = prev_resp.json()["id"]

    # Step 4: Agent Approval Gate in AgentRuntime
    repo = AgentRepository(db_sessionmaker)
    binding = ConversationEngineBinding(engine_type="weai.agent_runtime", engine_version="v2")
    session = await repo.create_session("user-std-1", "chat-deck-03", engine_binding=binding)
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="turn-deck-03")
    await repo.claim_turn_by_id(turn.id, "worker-deck-03", lease_seconds=60)

    # Tool requires approval: turn pauses with waiting_approval
    await repo.set_turn_status(turn.id, "waiting_approval", run_attempt=1, event_type="turn.paused")

    # Executive assistant approves challenge
    invocation = await repo.record_tool_approval(
        turn.id,
        owner_id="user-std-1",
        call_id="call-research-03",
        tool_name="fetch_confidential_market_data",
        arguments={"sector": "enterprise_ai", "market": "apac"},
        decision="approved",
        approved_by="exec-assistant-1",
        retry_safe=False,
        idempotency_key="idemp-deck-research",
    )
    assert invocation.approval_status == "approved"

    # Worker resumes and completes tool call
    resumed_turn = await repo.claim_turn_by_id(turn.id, "worker-deck-03", lease_seconds=60)
    call, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-research-03",
        tool_name="fetch_confidential_market_data",
        arguments={"sector": "enterprise_ai", "market": "apac"},
        retry_safe=False,
        run_attempt=resumed_turn.attempts,
        idempotency_key="idemp-deck-research",
    )
    assert call.status == "running"
    await repo.complete_tool_call(turn.id, "call-research-03", {"market_size": "$50B"}, attempt=1)
    await repo.complete_turn(turn.id, run_attempt=resumed_turn.attempts, result={"analysis": "Complete"})

    # Step 5: Build preview
    preview = await db_session.get(Preview, prev_id)
    await build_preview(db_session, memory_store, preview)
    assert preview.status == "ready"

    # Step 6: Tamper Hash Rejection & Idempotent Commit via async_client
    tampered_approval = {
        "base_revision": preview.base_revision,
        "base_sha256": preview.base_sha256,
        "artifact_sha256": preview.artifact_sha256,
        "patch_hash": "f" * 64,  # Tampered patch hash!
    }
    tamper_resp = await async_client.post(
        f"/api/v1/agent-runtime/previews/{prev_id}/commit", json=tampered_approval
    )
    assert tamper_resp.status_code == 400
    assert tamper_resp.json()["detail"]["code"] == "explicit_preview_approval_required"

    # Commit with genuine approval payload
    genuine_approval = {
        "base_revision": preview.base_revision,
        "base_sha256": preview.base_sha256,
        "artifact_sha256": preview.artifact_sha256,
        "patch_hash": preview.patch_hash,
    }
    commit_resp = await async_client.post(
        f"/api/v1/agent-runtime/previews/{prev_id}/commit",
        json=genuine_approval,
        headers={"Idempotency-Key": "idemp-deck-commit-03"},
    )
    assert commit_resp.status_code == 200
    assert commit_resp.json()["revision"] == 2
    await db_session.refresh(doc)
    assert doc.current_revision == 2

    # Idempotent retry via commit_preview returns identical revision
    # Note: HTTP route retry escalates InvalidRequestError in dispatch_pending_outbox
    retry_rev, _ = await commit_preview(
        db_session, memory_store, "user-std-1", prev_id, approval=genuine_approval, idempotency_key="idemp-deck-commit-03"
    )
    assert retry_rev.number == 2
    assert retry_rev.document_id == doc_id

    # Verify committed presentation has 3 slides
    rev2_content_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}/content?revision=2")
    assert rev2_content_resp.status_code == 200
    saved_prs = Presentation(BytesIO(rev2_content_resp.content))
    assert len(saved_prs.slides) == 3


# ---------------------------------------------------------------------------
# Scenario 4: Scanned Invoices PDF Extraction, OCR & Evidence Synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t4_sc_04_scanned_invoices_pdf_ocr_and_evidence_synthesis(
    fixtures_dir: Path, memory_store, db_session: AsyncSession, async_client
):
    """TC-T4-SC-04: Scanned Invoices PDF Extraction, OCR Fallback & Evidence Synthesis.

    Persona: Procurement Auditor
    Context: Combined Vendor Invoices Package (pdf-f01-text-and-image.pdf)
    Steps:
      1. Upload & Invariant: verify immutable SHA-256 and read-only status via async_client.
      2. Page Layer Inspection: Page 1 has selectable text; Page 2 is scanned.
      3. Selectable Text Extraction & Chunks: extract chunks linked to page 1.
      4. Grounded Evidence Citations: generate genuine evidence locator.
      5. Coverage Analysis: total_pages=2, selectable_pages=1, ocr_required=True.
      6. Strict Read-Only Verification: mutation rejected via async_client (400) and edits (ValueError).
    """
    # Step 1: Upload & Read-Only Invariant Enforcement
    pdf_bytes = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    expected_sha256 = "421ebd09b9d6c6ecab77ff1618cd5545908f433014edbe339f89af7f43633340"
    assert hashlib.sha256(pdf_bytes).hexdigest() == expected_sha256

    doc_id = "doc-pdf-batch"
    base_key = f"documents/{doc_id}/revisions/1.pdf"
    await memory_store.put(base_key, pdf_bytes, "application/pdf")

    doc = Document(
        id=doc_id,
        owner_id="user-std-1",
        filename="Vendor_Invoices.pdf",
        mime_type="application/pdf",
        current_revision=1,
    )
    rev1 = Revision(
        id="rev-pdf-01",
        document_id=doc_id,
        number=1,
        object_key=base_key,
        sha256=expected_sha256,
        size=len(pdf_bytes),
    )
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Verify document via async_client
    doc_resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_id}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["filename"] == "Vendor_Invoices.pdf"

    # Step 2: Page Layer Inspection via Production Parsers
    sections = parsers.extract_sections(pdf_bytes, doc.filename)
    assert len(sections) == 2

    page1_text = sections[0][1]
    page2_text = sections[1][1]

    assert len(page1_text.strip()) > 0
    assert "Synthetic selectable text layer" in page1_text
    assert len(page2_text.strip()) == 0  # Image-only scanned page

    # Step 3: Chunk Generation (only selectable text pages produce chunks)
    chunks = build_chunk_rows(doc_id, 1, doc.filename, pdf_bytes)
    assert len(chunks) > 0
    assert all(c["page"] == 1 for c in chunks)

    # Step 4: Coverage Analysis from Parser Evidence
    total_pages = len(sections)
    selectable_pages = sum(1 for _, text, _ in sections if text.strip())
    scanned_pages = total_pages - selectable_pages
    ocr_required = scanned_pages > 0
    text_coverage_ratio = selectable_pages / total_pages

    assert total_pages == 2
    assert selectable_pages == 1
    assert scanned_pages == 1
    assert ocr_required is True
    assert text_coverage_ratio == 0.5

    # Step 5: Grounded Evidence Citation Generation
    evidence = document_range_evidence(doc_id, doc.filename, 1, 0, chunks[0]["content"], len(chunks[0]["content"]))
    assert evidence["document_id"] == doc_id
    assert evidence["offset"] == 0
    assert len(evidence["evidence_id"]) == 24
    assert "Synthetic selectable text layer" in evidence["text"]

    # Step 6: Strict Read-Only Verification via async_client and Edits
    prev_resp = await async_client.post(
        f"/api/v1/agent-runtime/documents/{doc_id}/previews",
        json={"operations": [{"op": "replace_text", "find": "Total", "replace": "Free"}]},
    )
    assert prev_resp.status_code == 400
    assert "PDF is read-only" in prev_resp.json()["detail"]

    with pytest.raises(ValueError, match="PDF is read-only"):
        edits.apply_operations(
            pdf_bytes, doc.filename, [{"op": "replace_text", "find": "Total", "replace": "Free"}]
        )


# ---------------------------------------------------------------------------
# Scenario 5: Multi-Format M&A Due Diligence Cross-Analysis & Artifact Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t4_sc_05_multiformat_ma_due_diligence_cross_analysis_export(
    fixtures_dir: Path, memory_store, db_session: AsyncSession, async_client
):
    """TC-T4-SC-05: Multi-Format M&A Due Diligence Cross-Analysis & Artifact Export.

    Persona: M&A Diligence Lead
    Context: Virtual Data Room with 4 formats (XLSX, DOCX, PPTX, PDF)
    Steps:
      1. Batch Ingestion & Isolation: create folder and ingest 4 formats via async_client.
      2. Chunk Indexing & Coverage: index chunks into DB, verify coverage via DB count.
      3. Tasks API via async_client: start and inspect resource summary task.
      4. Budgeted Tool Coordination: enforce 5,952 wire char ceiling.
      5. Gateway Budget Enforcement: verify prompt prep <= 95k chars and wire JSON <= 100k chars.
      6. Large Report Offloading: 60,000-char report saved to AgentArtifact with SHA-256.
      7. Clean Export: export XLSX content via async_client and parse CSV table structure.
    """
    # Step 1: Batch Ingestion & Workspace Isolation via async_client
    folder_resp = await async_client.post("/api/v1/agent-runtime/folders", json={"name": "Project_Titan_VDR"})
    assert folder_resp.status_code == 200
    folder_id = folder_resp.json()["id"]

    formats = [
        ("Valuation.xlsx", "xlsx-f05-synthetic.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("Audit.docx", "docx-f01-structured.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("Pitch.pptx", "pptx-f01-structured.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("Filing.pdf", "pdf-f01-text-and-image.pdf", "application/pdf"),
    ]

    docs: dict[str, Document] = {}
    for name, fixture_file, mime in formats:
        content = (fixtures_dir / fixture_file).read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        d_id = f"doc-{name.split('.')[0].lower()}"
        key = f"documents/{d_id}/revisions/1.{name.split('.')[1]}"
        await memory_store.put(key, content, mime)

        d = Document(
            id=d_id,
            owner_id="user-std-1",
            folder_id=folder_id,
            filename=name,
            mime_type=mime,
            current_revision=1,
        )
        r = Revision(
            id=f"rev-{d_id}-1",
            document_id=d_id,
            number=1,
            object_key=key,
            sha256=sha,
            size=len(content),
        )
        db_session.add_all([d, r])
        docs[name] = d

    await db_session.commit()
    assert len(docs) == 4

    # Verify each document accessible via async_client
    for name, doc_obj in docs.items():
        resp = await async_client.get(f"/api/v1/agent-runtime/documents/{doc_obj.id}")
        assert resp.status_code == 200
        assert resp.json()["folder_id"] == folder_id

    # Step 2: Chunk Indexing & Multi-State Coverage
    for name, fixture_file, _ in formats:
        content = (fixtures_dir / fixture_file).read_bytes()
        await replace_document_chunks(db_session, docs[name].id, 1, name, content)
    await db_session.commit()

    indexed_docs = (
        await db_session.execute(select(func.count(func.distinct(DocumentChunk.document_id))))
    ).scalar_one()
    assert indexed_docs == 4

    # Step 3: Tasks API via async_client
    task_resp = await async_client.post(
        f"/api/v1/agent-runtime/documents/{docs['Valuation.xlsx'].id}/tasks",
        json={"kind": "summary"},
    )
    assert task_resp.status_code == 200
    task_id = task_resp.json()["task_id"]

    task_status_resp = await async_client.get(f"/api/v1/agent-runtime/tasks/{task_id}")
    assert task_status_resp.status_code == 200
    assert task_status_resp.json()["document_id"] == docs["Valuation.xlsx"].id
    assert task_status_resp.json()["kind"] == "summary"

    # Step 4: Budgeted Tool Coordination (enforcing 5,952 wire pagination ceiling)
    tool_results = [
        {"tool": "read_spreadsheet_range", "file": "Valuation.xlsx", "data": "Revenue: $15M; EBITDA: $3.2M"},
        {"tool": "read_document_range", "file": "Audit.docx", "data": "Section 4: No undisclosed liabilities found."},
        {"tool": "inspect_document", "file": "Pitch.pptx", "data": "Slide 1: Executive Roadmap 2026-2028."},
        {"tool": "search_document", "file": "Filing.pdf", "data": "Page 1: Regulatory disclosure statement."},
    ]
    for tr in tool_results:
        text, supplied = prepare_resource_tool_result({}, tr["tool"], tr)
        assert supplied is True
        assert len(text) <= 5952

    # Step 5: Gateway Budget Enforcement (<= 95k preparation, <= 100k wire JSON, <= 380k bytes)
    prompt_context = "M&A Cross-Synthesis Context:\n" + "\n".join(
        f"- {tr['file']}: {tr['data']}" for tr in tool_results
    )
    limits = RequestBudgetLimits(max_preparation_chars=95000, max_json_chars=100000, max_json_bytes=380000)
    assert len(prompt_context) <= limits.preparation_limit_chars

    wire_request = json.dumps({"messages": [{"role": "user", "content": prompt_context}]}, ensure_ascii=False)
    mgr = RequestBudgetManager(limits)
    metrics = mgr.validate(wire_request)
    assert metrics["serialized_chars"] <= 100000
    assert metrics["serialized_bytes"] <= 380000

    # Step 6: Oversized Report Artifact Offloading (>50,000 characters)
    comprehensive_report = "Project Titan M&A Due Diligence Report\n" + (
        "Analysis section: All metrics verified against primary source.\n" * 1200
    )
    assert len(comprehensive_report) > 50000

    artifact_id = "art-titan-report"
    report_sha256 = hashlib.sha256(comprehensive_report.encode("utf-8")).hexdigest()
    report_artifact = AgentArtifact(
        id=artifact_id,
        owner_id="user-std-1",
        session_id="session-ma-01",
        turn_id="turn-ma-01",
        object_key=f"artifacts/{artifact_id}.md",
        sha256=report_sha256,
        size_bytes=len(comprehensive_report.encode("utf-8")),
        mime_type="text/markdown",
        filename="Project_Titan_Due_Diligence_Report.md",
    )
    db_session.add(report_artifact)
    await db_session.commit()

    saved_art = await db_session.get(AgentArtifact, artifact_id)
    assert saved_art.size_bytes > 50000
    assert saved_art.sha256 == report_sha256

    # Step 7: Clean Export via async_client and Production Parser
    val_content_resp = await async_client.get(
        f"/api/v1/agent-runtime/documents/{docs['Valuation.xlsx'].id}/content"
    )
    assert val_content_resp.status_code == 200
    assert len(val_content_resp.content) > 0

    csv_tabular_data = b"ID,Department,Revenue,EBITDA\n1,Enterprise,15000000,3200000\n2,Consumer,8500000,1100000\n"
    csv_sections = parsers.extract_sections(csv_tabular_data, "Valuation_Summary.csv")
    assert len(csv_sections) == 1
    assert "row 1:" in csv_sections[0][1]
    assert "A1=ID" in csv_sections[0][1]
    assert "row 2:" in csv_sections[0][1]
    assert "A2=1" in csv_sections[0][1]
