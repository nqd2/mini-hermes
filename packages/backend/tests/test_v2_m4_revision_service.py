"""Milestone M4 Unit & Integration Tests: V2 Revision Service (M4.1 - M4.4).

Covers:
- M4.1: Publication state machine (`STAGED -> VERIFIED -> COMMITTED -> AVAILABLE`),
  immutable checksum-addressed blobs (`v2/blobs/{sha256}`), readback verification,
  abandoned staged object recovery preserving shared blobs, and fail-closed corrupted blob alerts.
- M4.2: D03b golden canonical patch hash & request fingerprint vectors, duplicate key / NaN rejection,
  strict format capability matrix allowlist for XLSX (formula guardrail, merged non-anchor rejection,
  `fullCalcOnLoad=True`), DOCX (`RunSpanMatcher` inline formatting preservation), PPTX (shape text edit &
  `GroupShape` rejection), and PDF (unconditional HTTP 422 `format_edit_unsupported`).
- M4.3: 15-minute approval binding, zero expiry extension on retry, LLM/AgentRuntime self-approval
  rejection, permission recheck at commit, patch hash mismatch, and concurrent edit conflict.
- M4.4: Single-transaction atomic publication (approval + revision + document pointer + outbox),
  idempotent commit replay & key conflict detection, and compensating rollback preserving history.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import openpyxl
import pytest
import pytest_asyncio
from docx import Document as PythonDocxDocument
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.models import (
    ApprovalStatus,
    MhAccessGrant,
    MhCompensatingRevision,
    MhDocument,
    MhDocumentApproval,
    MhDocumentRevision,
    MhEditPreview,
    MhIdempotencyRecord,
    MhObjectRegistry,
    MhOutbox,
    MhStagedObject,
    MhUser,
    PreviewStatus,
    PublicationState,
    V2Base,
)
from open_webui.mini_hermes.v2.revision import (
    ApprovalExpiredError,
    ApprovalInvalidError,
    IdempotencyConflictError,
    InvalidPatchPayloadError,
    InvalidStateTransitionError,
    PermissionDeniedError,
    RevisionConflictError,
    RevisionError,
    RevisionIntegrityFailureError,
    RevisionService,
    UnsupportedEditOperationError,
    apply_operations_in_memory,
    cleanup_abandoned_staged_objects,
    compute_canonical_patch_hash,
    compute_request_fingerprint,
    get_committed_revisions,
    parse_json_rejecting_duplicate_keys,
    serialize_canonical_json,
    stage_and_verify_artifact,
    transition_revision_state,
    validate_publication_transition,
    verify_committed_revision_bytes,
)
from open_webui.mini_hermes.v2.routes import (
    get_v2_db,
    get_v2_store,
    router as v2_router,
)
from open_webui.mini_hermes.v2.storage import V2ObjectStore


# ---------------------------------------------------------------------------
# Artifact Builders (Genuine Office Open XML & PDF binaries)
# ---------------------------------------------------------------------------

def build_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws["A1"] = "Revenue"
    ws["B1"] = 1000
    ws["B2"] = 500
    ws["B3"] = "=SUM(B1:B2)"
    ws.merge_cells("D1:E2")
    ws["D1"] = "MergedHeader"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_docx_bytes() -> bytes:
    doc = PythonDocxDocument()
    p = doc.add_paragraph()
    r1 = p.add_run("Project status is ")
    r2 = p.add_run("DRAFT")
    r2.bold = True
    r3 = p.add_run(" for Q4 review.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_pptx_bytes(*, with_group: bool = False) -> tuple[bytes, int, int | None]:
    prs = Presentation()
    slide_layout = prs.slide_layouts[6]  # blank layout
    slide = prs.slides.add_slide(slide_layout)
    tx_box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tx_box.text_frame.text = "Initial Slide Headline"
    text_shape_id = tx_box.shape_id

    group_shape_id: int | None = None
    if with_group:
        grp = slide.shapes.add_group_shape()
        child = grp.shapes.add_textbox(Inches(2), Inches(2), Inches(2), Inches(1))
        child.text_frame.text = "Grouped Child Text"
        group_shape_id = grp.shape_id

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue(), text_shape_id, group_shape_id


def build_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def create_db_env(tmp_path: Path):
    db_file = tmp_path / f"m4_revision_test_{uuid.uuid4().hex[:8]}.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(V2Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    store = V2ObjectStore(base_dir=tmp_path / f"v2_store_{uuid.uuid4().hex[:8]}")
    return engine, session_factory, store


async def seed_document(
    session: AsyncSession,
    store: V2ObjectStore,
    *,
    owner_id: str,
    name: str,
    format_type: str,
    mime_type: str,
    initial_bytes: bytes,
    now: datetime,
) -> tuple[MhDocument, MhDocumentRevision]:
    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    scoped_key, sha256 = store.put_immutable(initial_bytes)

    obj_reg = await session.get(MhObjectRegistry, sha256)
    if obj_reg is None:
        session.add(
            MhObjectRegistry(
                checksum_sha256=sha256,
                canonical_object_key=scoped_key,
                byte_size=len(initial_bytes),
                storage_tier="HOT",
                ref_count=1,
                gc_status="ACTIVE",
                gc_generation=1,
                last_verified_at=now,
                created_at=now,
            )
        )
    else:
        obj_reg.ref_count += 1

    doc = MhDocument(
        id=doc_id,
        tenant_id="default",
        name=name,
        owner_id=owner_id,
        folder_id=None,
        mime_type=mime_type,
        format_type=format_type,
        current_revision_id=rev_id,
        current_revision_no=1,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    rev = MhDocumentRevision(
        id=rev_id,
        document_id=doc_id,
        revision_no=1,
        object_key=scoped_key,
        checksum_sha256=sha256,
        byte_size=len(initial_bytes),
        source_metadata={"initial_seed": True},
        commit_status=PublicationState.COMMITTED.value,
        committed_by=owner_id,
        committed_at=now,
    )
    session.add(doc)
    session.add(rev)
    await session.commit()
    return doc, rev


# ---------------------------------------------------------------------------
# M4.1: Publication State Machine, Staged Blob Verification & GC Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_m4_1_publication_state_machine_and_stage_verification(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, session_factory, store = await create_db_env(tmp_path)
    now = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)

    # 1. Verify forward-only state machine transitions
    validate_publication_transition("STAGED", "VERIFIED")
    validate_publication_transition("VERIFIED", "COMMITTED")
    validate_publication_transition("COMMITTED", "AVAILABLE")

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_publication_transition("STAGED", "COMMITTED")
    assert exc_info.value.status_code == 409

    with pytest.raises(InvalidStateTransitionError):
        validate_publication_transition("COMMITTED", "STAGED")

    # 2. Verify staging writes immutable v2/blobs/{sha256} and transitions STAGED -> VERIFIED
    async with session_factory() as session:
        doc, rev1 = await seed_document(
            session,
            store,
            owner_id="owner-1",
            name="report.xlsx",
            format_type="xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            initial_bytes=build_xlsx_bytes(),
            now=now,
        )

        payload = b"staged-immutable-office-artifact-v2"
        expected_sha = hashlib.sha256(payload).hexdigest()
        staged_obj, scoped_key, actual_sha = await stage_and_verify_artifact(
            session,
            store,
            payload,
            document_id=doc.id,
            principal_id="owner-1",
            now_fn=lambda: now,
        )
        await session.commit()

        assert actual_sha == expected_sha
        assert scoped_key == f"v2/blobs/{expected_sha}"
        assert staged_obj.stage_status == PublicationState.VERIFIED.value
        assert staged_obj.verified_at == now

        # Staged object must NEVER appear as a committed revision
        committed_revs = await get_committed_revisions(session, doc.id)
        assert len(committed_revs) == 1
        assert committed_revs[0].revision_no == 1

        # Transition rev1 COMMITTED -> AVAILABLE
        await transition_revision_state(
            session,
            rev1,
            PublicationState.AVAILABLE,
            store=store,
        )
        assert rev1.commit_status == PublicationState.AVAILABLE.value

        # 3. Corrupt committed blob on disk and verify fail-closed + critical alert
        blob_path = store._local_path(rev1.object_key)
        original_bytes = blob_path.read_bytes()
        blob_path.write_bytes(b"corrupted-bytes-in-storage")

        with caplog.at_level(logging.CRITICAL):
            with pytest.raises(RevisionIntegrityFailureError) as integrity_exc:
                verify_committed_revision_bytes(store, rev1)

        assert integrity_exc.value.status_code == 503
        assert integrity_exc.value.code == "INTEGRITY_FAILURE"
        assert integrity_exc.value.details["legacy_code"] == "document_revision_integrity_failure"
        assert any("ALERT: Committed document revision integrity failure" in r.message for r in caplog.records)

        # Restore valid bytes
        blob_path.write_bytes(original_bytes)
        assert verify_committed_revision_bytes(store, rev1) == original_bytes
    await engine.dispose()


@pytest.mark.asyncio
async def test_m4_1_abandoned_staged_object_cleanup_preserves_shared_blobs(tmp_path: Path) -> None:
    engine, session_factory, store = await create_db_env(tmp_path)
    t0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
    xlsx_bytes = build_xlsx_bytes()

    async with session_factory() as session:
        doc, rev1 = await seed_document(
            session,
            store,
            owner_id="owner-1",
            name="shared.xlsx",
            format_type="xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            initial_bytes=xlsx_bytes,
            now=t0,
        )

        # Stage 1: shares identical SHA-256 with committed rev1
        shared_stage, shared_key, shared_sha = await stage_and_verify_artifact(
            session,
            store,
            xlsx_bytes,
            document_id=doc.id,
            principal_id="owner-1",
            now_fn=lambda: t0,
        )

        # Stage 2: unique unreferenced bytes that will become abandoned
        orphan_bytes = b"orphan-unreferenced-staged-blob-content"
        orphan_stage, orphan_key, orphan_sha = await stage_and_verify_artifact(
            session,
            store,
            orphan_bytes,
            document_id=doc.id,
            principal_id="owner-1",
            now_fn=lambda: t0,
        )
        await session.commit()

        # Advance clock past stage TTL (15m) + safety interval (1h)
        t_cleanup = t0 + timedelta(hours=2)
        gc_stats = await cleanup_abandoned_staged_objects(
            session,
            store,
            safety_interval_seconds=3600,
            now_fn=lambda: t_cleanup,
        )
        await session.commit()

        assert gc_stats["stages_cleaned"] == 2
        assert gc_stats["blobs_deleted"] == 1
        assert gc_stats["blobs_preserved"] == 1

        # Orphan blob must be physically removed; shared committed blob must remain intact!
        assert not store.exists(orphan_key)
        assert store.exists(shared_key)
        assert verify_committed_revision_bytes(store, rev1) == xlsx_bytes
    await engine.dispose()


# ---------------------------------------------------------------------------
# M4.2: D03b Canonical Patch Hash Vectors & Format Capability Matrix Allowlist
# ---------------------------------------------------------------------------

def test_m4_2_d03b_golden_hash_vectors_and_canonical_json_guards() -> None:
    fixtures_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "v2"
        / "phase0"
        / "fixtures"
        / "hash-vectors-v1.json"
    )
    vectors = json.loads(fixtures_path.read_text(encoding="utf-8"))

    # Vector 1: Patch Hash v1
    p_vec = next(v for v in vectors if v["kind"] == "patch")
    p_in = p_vec["payload"]
    computed_patch_hash = compute_canonical_patch_hash(
        document_id=p_in["document_id"],
        base_revision=p_in["base_revision"],
        base_sha256=p_in["base_sha256"],
        operations=p_in["operations"],
        artifact_sha256=p_in["artifact_sha256"],
    )
    assert computed_patch_hash == p_vec["sha256"]

    # Vector 2: Idempotency Request Fingerprint v1
    r_vec = next(v for v in vectors if v["kind"] == "request")
    r_in = r_vec["payload"]
    computed_req_fp = compute_request_fingerprint(
        principal_id=r_in["principal_id"],
        operation=r_in["operation"],
        request=r_in["request"],
    )
    assert computed_req_fp == r_vec["sha256"]

    # Reject duplicate keys, NaN, Infinity
    with pytest.raises(InvalidPatchPayloadError, match="Duplicate JSON object key"):
        parse_json_rejecting_duplicate_keys('{"op":"set_cell","op":"clear_cell"}')

    with pytest.raises(InvalidPatchPayloadError, match="Non-finite float"):
        serialize_canonical_json({"value": float("nan")})

    with pytest.raises(InvalidPatchPayloadError, match="Non-finite float"):
        serialize_canonical_json({"value": float("inf")})


def test_m4_2_format_capability_matrix_allowlist_and_in_memory_preview() -> None:
    # 1. XLSX: allowlisted set_cell_value succeeds and sets fullCalcOnLoad=True
    base_xlsx = build_xlsx_bytes()
    staged_xlsx, diff_xlsx, structured_xlsx = apply_operations_in_memory(
        format_type="xlsx",
        base_bytes=base_xlsx,
        operations=[
            {"op": "xlsx.set_cell_value", "sheet": "Financials", "cell": "B2", "value": 750}
        ],
    )
    assert "Financials!B2" in diff_xlsx
    assert structured_xlsx[0]["before"] == 500
    assert structured_xlsx[0]["after"] == 750

    wb_after = openpyxl.load_workbook(io.BytesIO(staged_xlsx), data_only=False)
    assert wb_after["Financials"]["B2"].value == 750
    assert wb_after.calculation.fullCalcOnLoad is True

    # Formula Protection Guardrail: overwriting formula cell B3 (=SUM(B1:B2)) is rejected (HTTP 422)
    with pytest.raises(RevisionError) as formula_exc:
        apply_operations_in_memory(
            format_type="xlsx",
            base_bytes=base_xlsx,
            operations=[
                {"op": "xlsx.set_cell_value", "sheet": "Financials", "cell": "B3", "value": 9999}
            ],
        )
    assert formula_exc.value.status_code == 422
    assert formula_exc.value.code == "formula_protection_violation"

    # Merged non-anchor cell E2 in D1:E2 is rejected (HTTP 422)
    with pytest.raises(RevisionError) as merged_exc:
        apply_operations_in_memory(
            format_type="xlsx",
            base_bytes=base_xlsx,
            operations=[
                {"op": "xlsx.set_cell_value", "sheet": "Financials", "cell": "E2", "value": "Bad"}
            ],
        )
    assert merged_exc.value.status_code == 422
    assert merged_exc.value.code == "merged_non_anchor_cell"

    # Unallowlisted XLSX operation (e.g. delete_sheet) is rejected (HTTP 422)
    with pytest.raises(UnsupportedEditOperationError) as bad_xlsx_op:
        apply_operations_in_memory(
            format_type="xlsx",
            base_bytes=base_xlsx,
            operations=[{"op": "xlsx.delete_sheet", "sheet": "Financials"}],
        )
    assert bad_xlsx_op.value.status_code == 422
    assert bad_xlsx_op.value.code == "UNSUPPORTED_EDIT_OPERATION"

    # 2. DOCX: RunSpanMatcher preserves bold <w:rPr> formatting
    base_docx = build_docx_bytes()
    staged_docx, diff_docx, _ = apply_operations_in_memory(
        format_type="docx",
        base_bytes=base_docx,
        operations=[
            {
                "op": "docx.replace_exact_text_in_paragraph",
                "paragraph_order": 1,
                "target_text": "DRAFT",
                "replacement_text": "APPROVED",
            }
        ],
    )
    assert "APPROVED" in diff_docx
    doc_after = PythonDocxDocument(io.BytesIO(staged_docx))
    assert doc_after.paragraphs[0].text == "Project status is APPROVED for Q4 review."
    bold_runs = [r for r in doc_after.paragraphs[0].runs if r.bold]
    assert any("APPROVED" in r.text for r in bold_runs)

    with pytest.raises(UnsupportedEditOperationError):
        apply_operations_in_memory(
            format_type="docx",
            base_bytes=base_docx,
            operations=[{"op": "docx.delete_section", "section": 1}],
        )

    # 3. PPTX: shape text replacement succeeds; GroupShape edit fails closed
    base_pptx, text_shape_id, group_shape_id = build_pptx_bytes(with_group=True)
    staged_pptx, diff_pptx, _ = apply_operations_in_memory(
        format_type="pptx",
        base_bytes=base_pptx,
        operations=[
            {
                "op": "pptx.replace_shape_text",
                "slide_order": 1,
                "shape_id": text_shape_id,
                "target_text": "Initial Slide Headline",
                "replacement_text": "Updated Executive Summary",
            }
        ],
    )
    assert "Updated Executive Summary" in diff_pptx
    prs_after = Presentation(io.BytesIO(staged_pptx))
    edited_shape = next(s for s in prs_after.slides[0].shapes if s.shape_id == text_shape_id)
    assert edited_shape.text_frame.text == "Updated Executive Summary"

    assert group_shape_id is not None
    with pytest.raises(RevisionError) as grp_exc:
        apply_operations_in_memory(
            format_type="pptx",
            base_bytes=base_pptx,
            operations=[
                {
                    "op": "pptx.replace_shape_text",
                    "slide_order": 1,
                    "shape_id": group_shape_id,
                    "target_text": "Grouped Child Text",
                    "replacement_text": "Blocked",
                }
            ],
        )
    assert grp_exc.value.status_code == 422
    assert "group" in grp_exc.value.code.lower()


# ---------------------------------------------------------------------------
# M4.3 & M4.4: 15m Approval Protocol, Atomic Publication & Rollback Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_m4_3_and_m4_4_approval_protocol_atomic_commit_and_rollback(tmp_path: Path) -> None:
    engine, session_factory, store = await create_db_env(tmp_path)
    t0 = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)
    owner_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())

    async with session_factory() as session:
        doc, rev1 = await seed_document(
            session,
            store,
            owner_id=owner_id,
            name="budget.xlsx",
            format_type="xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            initial_bytes=build_xlsx_bytes(),
            now=t0,
        )
        pdf_doc, _ = await seed_document(
            session,
            store,
            owner_id=owner_id,
            name="handbook.pdf",
            format_type="pdf",
            mime_type="application/pdf",
            initial_bytes=build_pdf_bytes(),
            now=t0,
        )

        # 1. PDF Read-Only Invariant: unconditional HTTP 422 format_edit_unsupported
        with pytest.raises(RevisionError) as pdf_exc:
            await RevisionService.create_preview(
                session,
                store,
                principal_id=owner_id,
                document_id=pdf_doc.id,
                operations=[{"op": "pdf.replace_text", "text": "x"}],
                now_fn=lambda: t0,
            )
        assert pdf_exc.value.status_code == 422
        assert pdf_exc.value.code == "format_edit_unsupported"

        # 2. Unauthorized user cannot create preview (HTTP 403 FORBIDDEN)
        with pytest.raises(PermissionDeniedError) as perm_exc:
            await RevisionService.create_preview(
                session,
                store,
                principal_id=other_user_id,
                document_id=doc.id,
                operations=[
                    {"op": "xlsx.set_cell_value", "sheet": "Financials", "cell": "B2", "value": 600}
                ],
                now_fn=lambda: t0,
            )
        assert perm_exc.value.status_code == 403

        # 3. Authorized owner creates preview with 15-minute approval window
        preview_res = await RevisionService.create_preview(
            session,
            store,
            principal_id=owner_id,
            document_id=doc.id,
            operations=[
                {"op": "xlsx.set_cell_value", "sheet": "Financials", "cell": "B2", "value": 600}
            ],
            idempotency_key="prev-idem-1",
            now_fn=lambda: t0,
        )
        preview_id = preview_res["preview_id"]
        patch_hash = preview_res["patch_hash"]
        expected_expiry_iso = (t0 + timedelta(minutes=15)).isoformat()
        assert preview_res["expires_at"] == expected_expiry_iso

        # 4. Retry at t0 + 5 minutes NEVER extends expires_at
        t_retry = t0 + timedelta(minutes=5)
        retried = await RevisionService.retry_preview(
            session,
            store,
            principal_id=owner_id,
            preview_id=preview_id,
            now_fn=lambda: t_retry,
        )
        assert retried["expires_at"] == expected_expiry_iso

        # 5. Reject LLM / AgentRuntime self-approval flags (HTTP 409)
        with pytest.raises(ApprovalInvalidError):
            await RevisionService.commit_preview(
                session,
                store,
                principal_id=owner_id,
                preview_id=preview_id,
                base_revision=1,
                patch_hash=patch_hash,
                idempotency_key="commit-llm-bypass",
                raw_approval_payload={"llm_approved": True},
                now_fn=lambda: t_retry,
            )

        # 6. Reject patch_hash mismatch (HTTP 409)
        with pytest.raises(ApprovalInvalidError) as hash_exc:
            await RevisionService.commit_preview(
                session,
                store,
                principal_id=owner_id,
                preview_id=preview_id,
                base_revision=1,
                patch_hash="f" * 64,
                idempotency_key="commit-bad-hash",
                now_fn=lambda: t_retry,
            )
        assert hash_exc.value.status_code == 409
        assert hash_exc.value.details.get("legacy_code") == "patch_hash_mismatch"

        # 7. Single-transaction atomic commit at t0 + 6 minutes
        t_commit = t0 + timedelta(minutes=6)
        commit_res = await RevisionService.commit_preview(
            session,
            store,
            principal_id=owner_id,
            preview_id=preview_id,
            base_revision=1,
            patch_hash=patch_hash,
            idempotency_key="commit-idem-1",
            now_fn=lambda: t_commit,
        )
        assert commit_res["revision_no"] == 2
        assert commit_res["commit_status"] == PublicationState.COMMITTED.value
        assert commit_res["idempotent_replay"] is False

        # Verify atomic DB state: approval CONSUMED, document at rev 2, outbox event created
        approval_row = (
            await session.execute(
                select(MhDocumentApproval).where(
                    MhDocumentApproval.preview_id == uuid.UUID(preview_id)
                )
            )
        ).scalar_one()
        assert approval_row.status == ApprovalStatus.CONSUMED.value
        assert approval_row.consumed_at is not None

        outbox_rows = list(
            (
                await session.execute(
                    select(MhOutbox).where(MhOutbox.aggregate_id == str(doc.id))
                )
            ).scalars().all()
        )
        assert len(outbox_rows) == 1
        assert outbox_rows[0].event_type == "document.revision.committed"

        # 8. Idempotent commit retry with same idempotency_key returns same revision without duplicate side effects
        replay_res = await RevisionService.commit_preview(
            session,
            store,
            principal_id=owner_id,
            preview_id=preview_id,
            base_revision=1,
            patch_hash=patch_hash,
            idempotency_key="commit-idem-1",
            now_fn=lambda: t_commit + timedelta(seconds=10),
        )
        assert replay_res["idempotent_replay"] is True
        assert replay_res["revision_id"] == commit_res["revision_id"]
        assert replay_res["revision_no"] == 2

        rev_count = (
            await session.execute(
                select(func.count(MhDocumentRevision.id)).where(
                    MhDocumentRevision.document_id == doc.id
                )
            )
        ).scalar()
        assert rev_count == 2

        outbox_count = (
            await session.execute(
                select(func.count(MhOutbox.id)).where(MhOutbox.aggregate_id == str(doc.id))
            )
        ).scalar()
        assert outbox_count == 1

        # Reusing idempotency_key with different payload raises HTTP 409 IDEMPOTENCY_CONFLICT
        with pytest.raises(IdempotencyConflictError) as idem_exc:
            await RevisionService.commit_preview(
                session,
                store,
                principal_id=owner_id,
                preview_id=preview_id,
                base_revision=2,
                patch_hash=patch_hash,
                idempotency_key="commit-idem-1",
                now_fn=lambda: t_commit + timedelta(seconds=20),
            )
        assert idem_exc.value.status_code == 409

        # 9. Compensating Rollback to revision 1 creates revision 3 and preserves history (revisions 1, 2, 3)
        t_rollback = t0 + timedelta(minutes=10)
        rb_res = await RevisionService.rollback_document(
            session,
            store,
            principal_id=owner_id,
            document_id=doc.id,
            target_revision=1,
            expected_current_revision=2,
            reason="Restore Q3 baseline",
            idempotency_key="rb-idem-1",
            now_fn=lambda: t_rollback,
        )
        assert rb_res["revision_no"] == 3
        assert rb_res["restored_from_revision_no"] == 1
        assert rb_res["faulty_revision_no"] == 2
        assert rb_res["checksum_sha256"] == rev1.checksum_sha256

        all_revs = await RevisionService.list_revisions(
            session,
            principal_id=owner_id,
            document_id=doc.id,
        )
        assert [r["revision_no"] for r in all_revs] == [1, 2, 3]
        assert all_revs[2]["checksum_sha256"] == all_revs[0]["checksum_sha256"]

        comp_records = list(
            (
                await session.execute(
                    select(MhCompensatingRevision).where(
                        MhCompensatingRevision.document_id == doc.id
                    )
                )
            ).scalars().all()
        )
        assert len(comp_records) == 1
        assert comp_records[0].faulty_revision_no == 2
        assert comp_records[0].compensating_revision_no == 3
    await engine.dispose()
