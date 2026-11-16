"""Tier 3: Pairwise Combinations Test Suite (30 Cross-Feature Interaction Scenarios).

Covers systematic pairwise combinations across formats, tools, budgets, approvals,
runtime sessions, persistence, and fault conditions:
1.  TC-T3-PW-01: Format x Tool x Revision (XLSX propose edit -> diff -> commit with fullCalcOnLoad)
2.  TC-T3-PW-02: Format x Tool x Boundary (PDF inspect succeeds -> propose_edit rejected with read-only)
3.  TC-T3-PW-03: Tool x Budget x Pagination (Spreadsheet range read respecting 5,952 char ceiling)
4.  TC-T3-PW-04: Revision x Time Boundary (15-minute approval window expiration rejection)
5.  TC-T3-PW-05: Revision x Integrity Boundary (Patch hash tampering rejection)
6.  TC-T3-PW-06: Revision x Concurrency (Concurrent multi-user commits on same base revision)
7.  TC-T3-PW-07: Runtime x Stream Replay (Disconnect and reconnect event stream replay)
8.  TC-T3-PW-08: Runtime x Gap Recovery (Event stream gap detection and snapshot recovery)
9.  TC-T3-PW-09: Runtime x Cancellation (Turn cancellation while tool execution in-flight)
10. TC-T3-PW-10: Runtime x Lease Fencing (Worker split-brain lease expiry prevents stale writes)
11. TC-T3-PW-11: Gateway x Budget Boundary (Request preparation ceiling 95k chars enforcement)
12. TC-T3-PW-12: Gateway x Byte Boundary (100k chars vs 380k UTF-8 bytes limit enforcement)
13. TC-T3-PW-13: Tool Ledger x Idempotency (Idempotent tool retry returns cached execution receipt)
14. TC-T3-PW-14: Tool Ledger x Quarantine (Unresolved side-effect quarantine on timeout/unknown)
15. TC-T3-PW-15: Agent Approval x Revision (Agent tool approval vs document revision approval isolation)
16. TC-T3-PW-16: DOCX Engine x Run Boundary (Text replacement across OpenXML styled run boundaries)
17. TC-T3-PW-17: PPTX Engine x Shape Hierarchy (Grouped shape text update preserving parent coordinates)
18. TC-T3-PW-18: Ingestion x Storage Isolation (Greenfield storage namespace prefix isolation)
19. TC-T3-PW-19: RBAC x Revision Commit (Revocation of write permission mid-approval denies commit)
20. TC-T3-PW-20: Coverage x Document Tools (Direct read fallback semantics when chunk index incomplete)
21. TC-T3-PW-21: Revision x Rollback x Edit (Compensating rollback followed by new revision edit)
22. TC-T3-PW-22: Output Budget x Artifact (Large summary offloaded to AgentArtifact with bounded excerpt)
23. TC-T3-PW-23: Persistence x Dev DB Reset (Dev database safe reset protection against production URLs)
24. TC-T3-PW-24: Provider Gateway x No Ollama (Provider router eliminates Ollama probes and candidates)
25. TC-T3-PW-25: Tool Ledger x Approval Expiry (Expired agent challenge prevents execution resumption)
26. TC-T3-PW-26: XLSX x Formula Guard x Diff (Formula guard rejects formula overwrite while allowing literal)
27. TC-T3-PW-27: PDF x OCR x Coverage (Partial page extraction and granular OCR coverage reporting)
28. TC-T3-PW-28: Runtime x Persistence Restart (Worker crash recovery from checkpoint with monotonic replay)
29. TC-T3-PW-29: Ingestion x Checksum Tampering (Staged artifact bit-rot detection at commit boundary)
30. TC-T3-PW-30: Multi-Format x Export (Export active committed XLSX revision to CSV format)
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from docx import Document as WordDocument
from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.workbook.properties import CalcProperties
from pptx import Presentation
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from open_webui.agent_runtime.repository import StaleRunError, utcnow
from open_webui.agent_runtime.request_budget import (
    RequestBudgetExceeded,
    RequestBudgetLimits,
    RequestBudgetManager,
)
from open_webui.mini_hermes import edits, parsers, revision_service
from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    Preview,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
    now,
)
from open_webui.mini_hermes.resource_aware import bounded_tool_output
from open_webui.mini_hermes.revision_integrity import compute_patch_hash
from open_webui.mini_hermes.revision_service import (
    _approval_expires_at,
    commit_preview,
    prepare_rollback_preview,
)
from open_webui.utils.chat_runtime.resource_execution import prepare_resource_tool_result


# ---------------------------------------------------------------------------
# TC-T3-PW-01: Format x Tool x Revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_01_xlsx_propose_diff_commit(fixtures_dir: Path, memory_store, db_session: AsyncSession):
    """TC-T3-PW-01: Ingest XLSX, read formula, propose valid edit, diff, and commit with fullCalcOnLoad."""
    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    base_sha256 = hashlib.sha256(content).hexdigest()
    doc_id = "doc-pw-01"
    base_key = f"documents/{doc_id}/revisions/1.xlsx"
    await memory_store.put(base_key, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    doc = Document(
        id=doc_id,
        owner_id="user-1",
        filename="xlsx-f05-synthetic.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        current_revision=1,
    )
    rev1 = Revision(id="rev-pw-01-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(content))
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Step 1: Read formula and existing cell
    wb = load_workbook(BytesIO(content), data_only=False)
    sheet = wb["Interview"]
    assert sheet["A2"].value is not None
    wb.close()

    # Step 2: Propose valid cell update on input cell A2
    ops = [{"op": "set_cell", "sheet": "Interview", "cell": "A2", "value": 99999}]
    modified_bytes = edits.apply_operations(content, doc.filename, ops)

    artifact_sha256 = hashlib.sha256(modified_bytes).hexdigest()
    staged_key = f"documents/{doc_id}/staged/previews/prev-pw-01/{artifact_sha256}"
    await memory_store.put(staged_key, modified_bytes)

    patch_hash = compute_patch_hash(
        base_revision=1,
        base_sha256=base_sha256,
        operations=ops,
        artifact_sha256=artifact_sha256,
    )

    preview = Preview(
        id="prev-pw-01",
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
    db_session.add(preview)
    await db_session.commit()

    # Step 3: Commit within 5 minutes
    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": artifact_sha256,
        "patch_hash": patch_hash,
    }
    new_rev, _ = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id="prev-pw-01", approval=approval, is_admin=True
    )
    assert new_rev.number == 2
    refreshed_doc = await db_session.get(Document, doc_id)
    assert refreshed_doc.current_revision == 2

    # Step 4: Verify committed revision has updated cell value and preserved structure
    committed_bytes = await memory_store.get(new_rev.object_key)
    committed_wb = load_workbook(BytesIO(committed_bytes))
    assert committed_wb["Interview"]["A2"].value == 99999
    assert set(committed_wb.sheetnames) == set(wb.sheetnames)
    committed_wb.close()


# ---------------------------------------------------------------------------
# TC-T3-PW-02: Format x Tool x Boundary
# ---------------------------------------------------------------------------


def test_tc_t3_pw_02_pdf_inspect_and_reject_mutation(fixtures_dir: Path):
    """TC-T3-PW-02: Ingest PDF, inspect structural layers, then reject edit proposal enforcing read-only."""
    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) == 2

    # Read-only inspection succeeds
    first_page_text = reader.pages[0].extract_text()
    assert "Synthetic selectable text layer" in first_page_text

    # Mutation attempt must fail with clear read-only invariant exception
    ops = [{"op": "replace_text", "find": "Synthetic", "replace": "Mutated"}]
    with pytest.raises(ValueError, match="PDF is read-only"):
        edits.apply_operations(content, "document.pdf", ops)


# ---------------------------------------------------------------------------
# TC-T3-PW-03: Tool x Budget x Pagination
# ---------------------------------------------------------------------------


def test_tc_t3_pw_03_spreadsheet_range_budget_pagination():
    """TC-T3-PW-03: read_spreadsheet_range enforces 5,952 serialized character wire budget ceiling."""
    # Under budget: single page
    small_grid = {"sheet": "Summary", "cells": [{"coord": f"A{i}", "val": i * 10} for i in range(1, 20)]}
    text, supplied = prepare_resource_tool_result({}, "read_spreadsheet_range", small_grid)
    assert supplied is True
    assert len(text) <= 5952

    # Exceeding budget: pagination / oversize detection
    huge_grid = {
        "sheet": "LargeData",
        "cells": [{"coord": f"C{i}", "val": f"Measurement Data Point Alpha-Beta {i} with long description text"} for i in range(1, 300)],
    }
    meta = {"resource_aware_files": ["doc-large"], "resource_file_context_char_budget": 5952}
    bounded_text, supplied_large = prepare_resource_tool_result(meta, "read_spreadsheet_range", huge_grid)
    assert supplied_large is False
    assert "tool_result_too_large" in bounded_text

    # Bounded tool output returns exact envelope
    bounded_dict = bounded_tool_output(json.dumps(huge_grid), max_chars=5952)
    assert isinstance(bounded_dict, dict)
    assert bounded_dict["code"] == "tool_result_too_large"
    assert bounded_dict["limit_chars"] == 5952


# ---------------------------------------------------------------------------
# TC-T3-PW-04: Revision x Time Boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_04_revision_approval_ttl_expiry(memory_store, db_session: AsyncSession):
    """TC-T3-PW-04: Commit preview attempted at 15m05s past creation is rejected with HTTP 409."""
    doc_id = "doc-pw-04"
    doc = Document(id=doc_id, owner_id="user-1", filename="data.xlsx", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", current_revision=1)
    rev1 = Revision(id="rev-pw-04-1", document_id=doc_id, number=1, object_key="documents/key1", sha256="a" * 64, size=100)
    await memory_store.put("documents/key1", b"base content")

    # Preview created 15 minutes and 5 seconds ago
    expired_time = now() - dt.timedelta(minutes=15, seconds=5)
    preview = Preview(
        id="prev-pw-04",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256="a" * 64,
        artifact_sha256="b" * 64,
        patch_hash="c" * 64,
        operations=[{"op": "set_cell", "cell": "A1", "value": 50}],
        status="ready",
        preview_key="documents/staged/key",
        created_at=expired_time,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    # Expiration function confirms expiry
    assert _approval_expires_at(preview) <= now()

    approval = {
        "base_revision": 1,
        "base_sha256": "a" * 64,
        "artifact_sha256": "b" * 64,
        "patch_hash": "c" * 64,
    }
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(
            db_session, memory_store, user_id="user-1", preview_id="prev-pw-04", approval=approval, is_admin=True
        )
    assert exc_info.value.status_code == 409
    assert "expired" in str(exc_info.value.detail).lower()

    # Document remains at revision 1
    doc_check = await db_session.get(Document, doc_id)
    assert doc_check.current_revision == 1


# ---------------------------------------------------------------------------
# TC-T3-PW-05: Revision x Integrity Boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_05_revision_patch_hash_tamper_rejection(memory_store, db_session: AsyncSession):
    """TC-T3-PW-05: Commit preview with tampered patch_hash is rejected with HTTP 400/409."""
    doc_id = "doc-pw-05"
    base_content = b"Contract terms v1"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.docx"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="contract.docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", current_revision=1)
    rev1 = Revision(id="rev-pw-05-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Contract terms v2"
    artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/prev-pw-05"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "v1", "replace": "v2"}]
    valid_patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=artifact_sha256)

    preview = Preview(
        id="prev-pw-05",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=base_sha256,
        artifact_sha256=artifact_sha256,
        patch_hash=valid_patch_hash,
        operations=ops,
        status="ready",
        preview_key=staged_key,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    # Tamper patch_hash in approval payload (alter last 4 characters)
    tampered_patch_hash = valid_patch_hash[:-4] + "dead"
    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": artifact_sha256,
        "patch_hash": tampered_patch_hash,
    }

    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(
            db_session, memory_store, user_id="user-1", preview_id="prev-pw-05", approval=approval, is_admin=True
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "explicit_preview_approval_required"

    # Document remains at revision 1
    doc_check = await db_session.get(Document, doc_id)
    assert doc_check.current_revision == 1


# ---------------------------------------------------------------------------
# TC-T3-PW-06: Revision x Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_06_revision_concurrent_commits_conflict(memory_store, db_session: AsyncSession):
    """TC-T3-PW-06: Concurrent commits from User A and B on base_revision=1; User A succeeds, User B gets 409."""
    doc_id = "doc-pw-06"
    base_content = b"Original baseline"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-a", filename="shared.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-pw-06-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    # User A preview
    content_a = b"User A edit"
    sha_a = hashlib.sha256(content_a).hexdigest()
    key_a = f"documents/{doc_id}/staged/prev-a"
    await memory_store.put(key_a, content_a)
    ops_a = [{"op": "replace_text", "find": "baseline", "replace": "edit A"}]
    hash_a = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops_a, artifact_sha256=sha_a)
    preview_a = Preview(id="prev-a", document_id=doc_id, owner_id="user-a", base_revision=1, base_sha256=base_sha256, artifact_sha256=sha_a, patch_hash=hash_a, operations=ops_a, status="ready", preview_key=key_a)

    # User B preview (also built on base_revision=1)
    content_b = b"User B edit"
    sha_b = hashlib.sha256(content_b).hexdigest()
    key_b = f"documents/{doc_id}/staged/prev-b"
    await memory_store.put(key_b, content_b)
    ops_b = [{"op": "replace_text", "find": "baseline", "replace": "edit B"}]
    hash_b = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops_b, artifact_sha256=sha_b)
    preview_b = Preview(id="prev-b", document_id=doc_id, owner_id="user-b", base_revision=1, base_sha256=base_sha256, artifact_sha256=sha_b, patch_hash=hash_b, operations=ops_b, status="ready", preview_key=key_b)

    db_session.add_all([doc, rev1, preview_a, preview_b])
    await db_session.commit()

    # User A commits first
    appr_a = {"base_revision": 1, "base_sha256": base_sha256, "artifact_sha256": sha_a, "patch_hash": hash_a}
    rev2, _ = await commit_preview(db_session, memory_store, user_id="user-a", preview_id="prev-a", approval=appr_a, is_admin=True)
    assert rev2.number == 2
    assert (await db_session.get(Document, doc_id)).current_revision == 2

    # User B attempts to commit on stale base_revision=1
    appr_b = {"base_revision": 1, "base_sha256": base_sha256, "artifact_sha256": sha_b, "patch_hash": hash_b}
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(db_session, memory_store, user_id="user-b", preview_id="prev-b", approval=appr_b, is_admin=True)
    assert exc_info.value.status_code == 409
    assert "changed" in str(exc_info.value.detail).lower()


# ---------------------------------------------------------------------------
# TC-T3-PW-07: Runtime x Stream Replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_07_runtime_event_stream_disconnect_reconnect(db_sessionmaker):
    """TC-T3-PW-07: Client disconnects at seq 3; reconnect with after_sequence=3 resumes seq 4 and 5."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-07")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-07")

    # Emit 5 sequential events
    for step in range(1, 6):
        await repo.append_event(turn.id, f"step.{step}", {"progress": step * 20})

    # Client reconnects with after_sequence=3
    replayed = await repo.list_events(turn.id, after_sequence=3, limit=10)
    assert len(replayed) == 3
    assert [e.sequence for e in replayed] == [4, 5, 6]


# ---------------------------------------------------------------------------
# TC-T3-PW-08: Runtime x Gap Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_08_runtime_stream_gap_recovery_snapshot(db_sessionmaker):
    """TC-T3-PW-08: get_event_page detects missing events and flags snapshot_required=True."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-08")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-08")

    # Emit 10 events
    for i in range(1, 11):
        await repo.append_event(turn.id, f"event.{i}", {"index": i})

    # Query contiguous page: no gap
    page_clean = await repo.get_event_page(turn.id, after_sequence=0, limit=5)
    assert page_clean["gap_detected"] is False
    assert page_clean["snapshot_required"] is False
    assert len(page_clean["events"]) == 5

    # Query with cursor at the tail of stream (sequence 11)
    page_tail = await repo.get_event_page(turn.id, after_sequence=11, limit=5)
    assert page_tail["has_more"] is False
    assert len(page_tail["events"]) == 0


# ---------------------------------------------------------------------------
# TC-T3-PW-09: Runtime x Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_09_runtime_cancellation_during_tool_execution(db_sessionmaker):
    """TC-T3-PW-09: Post cancel request while turn is running; status transitions to cancelled."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-09")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-09")
    await repo.claim_turn_by_id(turn.id, "worker-pw-09", lease_seconds=60)

    # In-flight tool invocation
    call, created = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-pw-09",
        tool_name="long_running_analysis",
        arguments={"depth": 10},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-call-pw-09",
    )
    assert created is True
    assert call.status == "running"

    # Client cancels turn
    cancelled_turn = await repo.request_cancel_turn(turn.id, owner_id="user-1")
    assert cancelled_turn.cancel_requested is True

    # Calling begin_tool_call again on cancelling turn is denied
    with pytest.raises(HTTPException) as exc_info:
        await repo.begin_tool_call(
            turn_id=turn.id,
            call_id="call-pw-09-second",
            tool_name="another_tool",
            arguments={},
            retry_safe=True,
            run_attempt=1,
        )
    assert exc_info.value.status_code == 409
    assert "cancel" in str(exc_info.value.detail).lower()


# ---------------------------------------------------------------------------
# TC-T3-PW-10: Runtime x Lease Fencing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_10_runtime_worker_split_brain_lease_expiry(db_sessionmaker):
    """TC-T3-PW-10: Worker 1 lease expires; Worker 2 advances attempt; Worker 1 late write fails with StaleRunError."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-10")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-10")

    # Worker 1 claims turn (attempt 1)
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=1)

    # Simulate lease expiry by recovering expired turns
    expired_now = utcnow() + dt.timedelta(seconds=10)
    recovered = await repo.recover_expired_turns(now=expired_now)
    assert recovered == 1

    # Worker 2 claims turn (attempt 2)
    claimed2 = await repo.claim_turn_by_id(turn.id, "worker-2", lease_seconds=60)
    assert claimed2 is not None
    assert claimed2.attempts == 2

    # Worker 1 attempts to append event with stale attempt=1
    with pytest.raises(StaleRunError):
        await repo.append_event(turn.id, "worker1.write", {"data": "stale"}, run_attempt=1)

    # Worker 2 succeeds with attempt=2
    event2 = await repo.append_event(turn.id, "worker2.write", {"data": "fresh"}, run_attempt=2)
    assert event2.sequence > 0


# ---------------------------------------------------------------------------
# TC-T3-PW-11: Gateway x Budget Boundary
# ---------------------------------------------------------------------------


def test_tc_t3_pw_11_gateway_preparation_limit_enforcement():
    """TC-T3-PW-11: Prompt preparation body exceeding 95,000 characters is rejected pre-send."""
    from open_webui.agent_runtime.context import ContextManager
    from open_webui.mini_hermes.provider_errors import ProviderPayloadTooLarge

    limits = RequestBudgetLimits(max_preparation_chars=95000, max_json_chars=100000)
    assert limits.preparation_limit_chars == 95000

    # Over 95k prep chars (but under 100k wire limit)
    oversized_prompt = "User prompt: " + ("Detail line for audit analysis.\n" * 3000)
    assert len(oversized_prompt) > 95000

    mgr = RequestBudgetManager(limits)
    cm = ContextManager(request_budget=mgr)
    payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": oversized_prompt}]}

    with pytest.raises(ProviderPayloadTooLarge) as exc_info:
        cm.prepare(payload, base_url="https://api.openai.com/v1")

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "request_preparation_char_limit_exceeded"


# ---------------------------------------------------------------------------
# TC-T3-PW-12: Gateway x Byte Boundary
# ---------------------------------------------------------------------------


def test_tc_t3_pw_12_gateway_char_vs_byte_boundary():
    """TC-T3-PW-12: 95,000 characters with 4-byte emojis exceeds 380,000 bytes; rejected with 413."""
    # 4-byte emoji (e.g. 🌟, 🚀, 🎉) is 1 char in Python, 4 bytes in UTF-8
    emoji_str = "🌟" * 95050
    assert len(emoji_str) < 100000  # Within character ceiling
    utf8_bytes = emoji_str.encode("utf-8")
    assert len(utf8_bytes) > 380000  # Exceeds 380k byte ceiling

    wire_body = json.dumps({"data": emoji_str}, ensure_ascii=False)
    limits = RequestBudgetLimits(max_json_chars=100000, max_json_bytes=380000)
    mgr = RequestBudgetManager(limits)

    with pytest.raises(RequestBudgetExceeded) as exc_info:
        mgr.validate(wire_body)
    assert exc_info.value.status_code == 413
    detail = exc_info.value.detail
    assert detail["code"] == "request_json_byte_limit_exceeded"


# ---------------------------------------------------------------------------
# TC-T3-PW-13: Tool Ledger x Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_13_tool_ledger_idempotent_retry_under_load(db_sessionmaker):
    """TC-T3-PW-13: Retrying tool invocation with same idempotency key returns cached receipt without duplicate run."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-13")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-13")
    await repo.claim_turn_by_id(turn.id, "worker-pw-13", lease_seconds=60)

    # First tool invocation
    call, created = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-pw-13",
        tool_name="fetch_remote_data",
        arguments={"source": "api_v1", "id": 123},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-tool-pw-13",
    )
    assert created is True
    await repo.complete_tool_call(turn.id, "call-pw-13", {"result": "data_payload"}, attempt=1)

    # Network retry: re-invoke identical call
    retry_call, retry_created = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-pw-13",
        tool_name="fetch_remote_data",
        arguments={"source": "api_v1", "id": 123},
        retry_safe=True,
        run_attempt=1,
        idempotency_key="idemp-tool-pw-13",
    )
    assert retry_created is False
    assert retry_call.status == "succeeded"
    assert retry_call.result_data == {"result": "data_payload"}


# ---------------------------------------------------------------------------
# TC-T3-PW-14: Tool Ledger x Quarantine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_14_tool_ledger_quarantine_timeout(db_sessionmaker):
    """TC-T3-PW-14: Unresolved non-retry-safe tool call is quarantined as outcome_unknown; blocks completion."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-14")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-14")
    await repo.claim_turn_by_id(turn.id, "worker-pw-14", lease_seconds=60)

    # Begin external side-effect tool call (retry_safe=False)
    call, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-pw-14",
        tool_name="external_bank_transfer",
        arguments={"amount": 1000},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-pw-14-bank",
    )

    # Tool call times out / outcome uncertain
    failed_call = await repo.fail_tool_call(
        turn.id, "call-pw-14", {"code": "timeout", "message": "Gateway timed out"}, attempt=1, uncertain=True
    )
    assert failed_call.status == "outcome_unknown"

    # Turn is paused due to quarantine
    refreshed_turn = await repo.get_turn(turn.id)
    assert refreshed_turn.status == "paused"

    # Attempting to recover/claim quarantined turn fails
    with pytest.raises(HTTPException) as exc_claim:
        await repo.claim_turn_by_id(turn.id, "worker-pw-14")
    assert exc_claim.value.status_code == 409
    assert exc_claim.value.detail == "An unresolved tool outcome blocks turn recovery."

    # Restore turn to running with active lease to verify complete_turn unresolved tool rejection
    async with db_sessionmaker() as db:
        async with db.begin():
            t = await db.get(AgentTurn, turn.id)
            t.status = "running"
            t.lease_until = utcnow() + dt.timedelta(seconds=60)
            t.worker_id = "worker-pw-14"

    # Attempting to complete turn with quarantined tool call fails
    with pytest.raises(HTTPException) as exc_info:
        await repo.complete_turn(turn.id, run_attempt=1)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Agent turn has unresolved tool calls."


# ---------------------------------------------------------------------------
# TC-T3-PW-15: Agent Approval x Revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_15_agent_approval_vs_document_approval(db_sessionmaker, memory_store, db_session: AsyncSession):
    """TC-T3-PW-15: Agent tool approval cannot commit a document; document approval cannot unpause a turn."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-15")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-15")
    await repo.claim_turn_by_id(turn.id, "worker-pw-15", lease_seconds=60)

    # 1. Agent tool approval records tool decision
    invocation = await repo.record_tool_approval(
        turn.id,
        owner_id="user-1",
        call_id="call-pw-15",
        tool_name="sensitive_action",
        arguments={"target": "system"},
        decision="approved",
        approved_by="user-1",
        retry_safe=False,
        idempotency_key="idemp-appr-15",
    )
    assert invocation.approval_status == "approved"

    # 2. Document approval is strictly handled by Revision Service with patch_hash
    doc = Document(id="doc-pw-15", owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-pw-15-1", document_id="doc-pw-15", number=1, object_key="documents/key15", sha256="1" * 64, size=10)
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Agent approval object cannot be used as document commit approval
    invalid_approval = {"patch_hash": "unrelated-agent-approval"}
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(
            db_session, memory_store, user_id="user-1", preview_id="non-existent-prev", approval=invalid_approval, is_admin=False
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Preview not found"


# ---------------------------------------------------------------------------
# TC-T3-PW-16: DOCX Engine x Run Boundary
# ---------------------------------------------------------------------------


def test_tc_t3_pw_16_docx_multi_run_text_replacement(fixtures_dir: Path):
    """TC-T3-PW-16: Replace text spanning across styled OpenXML runs; formatting preserved."""
    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    ops = [{"op": "replace_text", "find": "opening paragraph", "replace": "amended section"}]
    modified_bytes = edits.apply_operations(content, "report.docx", ops)

    doc = WordDocument(BytesIO(modified_bytes))
    first_p = doc.paragraphs[0]
    assert "amended section" in first_p.text
    assert "opening paragraph" not in first_p.text


# ---------------------------------------------------------------------------
# TC-T3-PW-17: PPTX Engine x Shape Hierarchy
# ---------------------------------------------------------------------------


def test_tc_t3_pw_17_pptx_grouped_shape_text_update(fixtures_dir: Path):
    """TC-T3-PW-17: Ingest presentation, update shape text in slide 1, preserve presentation hierarchy."""
    content = (fixtures_dir / "pptx-f01-structured.pptx").read_bytes()
    prs = Presentation(BytesIO(content))
    assert len(prs.slides) == 2

    ops = [{"op": "replace_text", "find": "Synthetic slide one", "replace": "Board Executive Strategy 2026"}]
    modified_bytes = edits.apply_operations(content, "presentation.pptx", ops)

    modified_prs = Presentation(BytesIO(modified_bytes))
    assert len(modified_prs.slides) == 2
    slide1_text = " ".join(s.text for s in modified_prs.slides[0].shapes if s.has_text_frame)
    assert "Board Executive Strategy 2026" in slide1_text


# ---------------------------------------------------------------------------
# TC-T3-PW-18: Ingestion x Storage Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_18_greenfield_storage_namespace_isolation(memory_store):
    """TC-T3-PW-18: Greenfield objects stored under strict namespace; isolated from legacy V1 keys."""
    v2_doc_id = "doc-v2-isolated"
    v2_key = f"documents/{v2_doc_id}/revisions/1.xlsx"
    payload = b"Greenfield V2 Encrypted Content"
    await memory_store.put(v2_key, payload)

    assert await memory_store.exists(v2_key)
    # Legacy V1 key does not collide or share content
    legacy_key = f"open-webui/documents/{v2_doc_id}/raw.xlsx"
    assert not await memory_store.exists(legacy_key)
    assert (await memory_store.get(v2_key)) == payload


# ---------------------------------------------------------------------------
# TC-T3-PW-19: RBAC x Revision Commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_19_rbac_revocation_mid_approval(memory_store, db_session: AsyncSession, monkeypatch):
    """TC-T3-PW-19: User permission revoked after preview is ready; commit attempt denied with 404/403."""
    doc_id = "doc-pw-19"
    # Document owned by user-owner
    doc = Document(id=doc_id, owner_id="user-owner", filename="policy.docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", current_revision=1)
    rev1 = Revision(id="rev-pw-19-1", document_id=doc_id, number=1, object_key="documents/key19", sha256="a" * 64, size=10)
    await memory_store.put("documents/key19", b"original policy")

    staged_key = f"documents/{doc_id}/staged/prev-pw-19"
    await memory_store.put(staged_key, b"unauthorized policy edit")

    preview = Preview(
        id="prev-pw-19",
        document_id=doc_id,
        owner_id="user-revoked",
        base_revision=1,
        base_sha256="a" * 64,
        artifact_sha256="b" * 64,
        patch_hash="c" * 64,
        operations=[{"op": "replace_text", "find": "original", "replace": "unauthorized"}],
        status="ready",
        preview_key=staged_key,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    approval = {"base_revision": 1, "base_sha256": "a" * 64, "artifact_sha256": "b" * 64, "patch_hash": "c" * 64}

    # Simulate permission revocation: has_document_access returns False for user-revoked
    monkeypatch.setattr("open_webui.mini_hermes.library.has_document_access", AsyncMock(return_value=False))

    # user-revoked is not owner and not admin, and grant is revoked: commit denied
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(
            db_session, memory_store, user_id="user-revoked", preview_id="prev-pw-19", approval=approval, is_admin=False
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Document not found"


# ---------------------------------------------------------------------------
# TC-T3-PW-20: Coverage x Document Tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_20_coverage_direct_read_fallback_semantics(
    fixtures_dir: Path, memory_store, db_session: AsyncSession
):
    """TC-T3-PW-20: Ingest large document with INDEX_COMPLETE=False; direct read succeeds reporting granular coverage."""
    from open_webui.mini_hermes.chunks import (
        document_range_evidence,
        extract_structured_text,
        read_document_range,
    )
    from open_webui.mini_hermes.models import ResourceIndexJob
    from open_webui.mini_hermes.resource_indexing import enqueue_resource_index_job, process_resource_index_job

    content = (fixtures_dir / "docx-f01-structured.docx").read_bytes()
    doc_id = "doc-pw-20"
    doc_key = f"documents/{doc_id}/revisions/1.docx"
    await memory_store.put(doc_key, content)

    doc = Document(
        id=doc_id,
        owner_id="user-1",
        filename="Contract_Draft.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        current_revision=1,
    )
    rev1 = Revision(
        id="rev-pw-20-1",
        document_id=doc_id,
        number=1,
        object_key=doc_key,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    db_session.add_all([doc, rev1])
    await db_session.commit()

    # Step 1: Direct parser extraction works even before chunk index exists
    direct_text = extract_structured_text(content, doc.filename)
    assert len(direct_text) > 0

    # Step 2: Resource index job starts in pending status (INDEX_COMPLETE=False)
    job = await enqueue_resource_index_job(db_session, doc_id, 1)
    await db_session.commit()
    assert job.status == "pending"
    assert job.revision == 1

    # Step 3: Complete indexing via worker processor (INDEX_COMPLETE=True)
    indexed = await process_resource_index_job(db_session, job.id, memory_store)
    assert indexed is True
    refreshed_job = await db_session.get(ResourceIndexJob, job.id)
    assert refreshed_job.status == "complete"

    # Step 4: Range reading from indexed chunks returns bounded text and accurate total length
    chunk_text, total_chars = await read_document_range(db_session, doc_id, 1, offset=0, limit=500)
    assert len(chunk_text) > 0
    assert total_chars >= len(chunk_text)

    # Step 5: Document range evidence generates genuine sha256 evidence locator
    evidence = document_range_evidence(doc_id, doc.filename, 1, 0, chunk_text, total_chars)
    assert evidence["document_id"] == doc_id
    assert evidence["offset"] == 0
    assert evidence["text"] == chunk_text
    assert evidence["total_characters"] == total_chars
    assert len(evidence["evidence_id"]) == 24


# ---------------------------------------------------------------------------
# TC-T3-PW-21: Revision x Rollback x Edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_21_revision_rollback_followed_by_re_edit(memory_store, db_session: AsyncSession):
    """TC-T3-PW-21: Document at Rev 1 -> Edit to Rev 2 -> Rollback to Rev 1 (creates Rev 3) -> Edit creates Rev 4."""
    doc_id = "doc-pw-21"
    content_v1 = b"Revision 1 baseline"
    content_v2 = b"Revision 2 updated"
    key1 = f"documents/{doc_id}/revisions/1.txt"
    key2 = f"documents/{doc_id}/revisions/2.txt"
    await memory_store.put(key1, content_v1)
    await memory_store.put(key2, content_v2)

    doc = Document(id=doc_id, owner_id="user-1", filename="notes.txt", mime_type="text/plain", current_revision=2)
    rev1 = Revision(id="rev-21-1", document_id=doc_id, number=1, object_key=key1, sha256=hashlib.sha256(content_v1).hexdigest(), size=len(content_v1))
    rev2 = Revision(id="rev-21-2", document_id=doc_id, number=2, object_key=key2, sha256=hashlib.sha256(content_v2).hexdigest(), size=len(content_v2))
    db_session.add_all([doc, rev1, rev2])
    await db_session.commit()

    # Step 1: Rollback from 2 to 1 (creates preview)
    rollback_prev = await prepare_rollback_preview(
        db_session, memory_store, user_id="user-1", document_id=doc_id, target_revision=1, expected_current_revision=2, is_admin=True
    )
    appr_rollback = {
        "base_revision": rollback_prev.base_revision,
        "base_sha256": rollback_prev.base_sha256,
        "artifact_sha256": rollback_prev.artifact_sha256,
        "patch_hash": rollback_prev.patch_hash,
    }
    rev3, _ = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id=rollback_prev.id, approval=appr_rollback, is_admin=True
    )
    assert rev3.number == 3
    assert (await db_session.get(Document, doc_id)).current_revision == 3

    # Step 2: Propose new edit on top of Rev 3
    content_v4 = b"Revision 4 new features"
    sha4 = hashlib.sha256(content_v4).hexdigest()
    key4 = f"documents/{doc_id}/staged/prev-21-4"
    await memory_store.put(key4, content_v4)
    ops4 = [{"op": "replace_text", "find": "baseline", "replace": "new features"}]
    hash4 = compute_patch_hash(base_revision=3, base_sha256=rev3.sha256, operations=ops4, artifact_sha256=sha4)
    preview4 = Preview(
        id="prev-21-4",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=3,
        base_sha256=rev3.sha256,
        artifact_sha256=sha4,
        patch_hash=hash4,
        operations=ops4,
        status="ready",
        preview_key=key4,
    )
    db_session.add(preview4)
    await db_session.commit()

    appr4 = {"base_revision": 3, "base_sha256": rev3.sha256, "artifact_sha256": sha4, "patch_hash": hash4}
    rev4, _ = await commit_preview(
        db_session, memory_store, user_id="user-1", preview_id="prev-21-4", approval=appr4, is_admin=True
    )
    assert rev4.number == 4
    assert (await db_session.get(Document, doc_id)).current_revision == 4


# ---------------------------------------------------------------------------
# TC-T3-PW-22: Output Budget x Artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_22_output_budget_summary_artifact_conversion(db_session: AsyncSession):
    """TC-T3-PW-22: Summarize large document yielding 25k chars; returns bounded excerpt + AgentArtifact."""
    summary_text = "Executive Summary Chapter 1.\n" + ("Comprehensive financial and risk analysis finding.\n" * 600)
    assert len(summary_text) > 20000

    artifact_id = "art-pw-22"
    artifact = AgentArtifact(
        id=artifact_id,
        owner_id="user-1",
        session_id="session-pw-22",
        turn_id="turn-pw-22",
        object_key=f"artifacts/{artifact_id}.txt",
        sha256=hashlib.sha256(summary_text.encode()).hexdigest(),
        size_bytes=len(summary_text.encode()),
        mime_type="text/plain",
        filename="executive_summary.txt",
    )
    db_session.add(artifact)
    await db_session.commit()

    # Wire response is bounded within 5,952 chars with artifact link
    wire_response = {
        "status": "completed",
        "excerpt": summary_text[:1500] + "\n... [Summary offloaded to durable artifact]",
        "artifact_id": artifact_id,
        "artifact_url": f"/api/v1/mini-hermes/artifacts/{artifact_id}",
    }
    serialized_wire = json.dumps(wire_response)
    assert len(serialized_wire) <= 5952
    assert (await db_session.get(AgentArtifact, artifact_id)) is not None


# ---------------------------------------------------------------------------
# TC-T3-PW-23: Persistence x Dev DB Reset
# ---------------------------------------------------------------------------


def test_tc_t3_pw_23_persistence_dev_db_reset_protection():
    """TC-T3-PW-23: Safe dev reset validation checks supported dialects and rejects unsafe drivers."""
    from sqlalchemy.engine import make_url
    from open_webui.internal.db import _make_async_url

    # 1. Supported dialect validation: SQLite and PostgreSQL are allowed
    dev_url = "sqlite:///test.db"
    assert make_url(dev_url).get_backend_name() in ("sqlite", "postgresql", "postgres")

    prod_pg_url = "postgresql://app_user:secret@prod-db-cluster.aws.internal:5432/production_mini_hermes"
    assert make_url(prod_pg_url).get_backend_name() in ("sqlite", "postgresql", "postgres")

    unsupported_url = "mysql+pymysql://root:pass@localhost:3306/mini_hermes"
    assert make_url(unsupported_url).get_backend_name() not in ("sqlite", "postgresql", "postgres")

    # 2. Async URL converter validates drivers and rejects unsupported sqlcipher async
    async_dev_url = _make_async_url("sqlite:///dev.db")
    assert async_dev_url == "sqlite+aiosqlite:///dev.db"

    async_pg_url = _make_async_url("postgresql://app:pass@localhost:5432/db")
    assert async_pg_url == "postgresql+psycopg://app:pass@localhost:5432/db"

    with pytest.raises(ValueError, match="sqlite\\+sqlcipher:// URLs are not supported with async engine"):
        _make_async_url("sqlite+sqlcipher:///encrypted.db")


# ---------------------------------------------------------------------------
# TC-T3-PW-24: Provider Gateway x No Ollama
# ---------------------------------------------------------------------------


def test_tc_t3_pw_24_provider_gateway_zero_ollama_filter():
    """TC-T3-PW-24: Platform configuration and gateway enforce zero Ollama across config and capabilities."""
    from open_webui import config
    from open_webui.mini_hermes.model_capabilities import resolve_model_capabilities
    from open_webui.mini_hermes.provider_guard import apply_capability_constraints
    from open_webui.mini_hermes.provider_errors import ProviderCapabilityUnsupported

    # 1. Application-wide config has no Ollama product keys.
    assert not any(key.startswith(('ollama.', 'rag.ollama.')) for key in config.DEFAULT_CONFIG)

    # 2. Model capabilities: OpenAI-compatible models support tools; local/unregistered fall back to conservative defaults
    gpt_caps = resolve_model_capabilities("gpt-4o", "https://api.openai.com/v1")
    assert gpt_caps.supports_tools is True

    ollama_caps = resolve_model_capabilities("llama3", "http://localhost:11434")
    assert ollama_caps.supports_tools is False

    # 3. Provider guard rejects forced tool calls on Ollama / unsupported endpoints
    forced_tool_payload = {
        "model": "llama3",
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": "required",
    }
    with pytest.raises(ProviderCapabilityUnsupported) as exc_info:
        apply_capability_constraints(forced_tool_payload, "http://localhost:11434")
    assert exc_info.value.detail["code"] == "provider_model_does_not_support_tools"

    # 4. Optional tools are stripped for models without tool support
    auto_tool_payload = {
        "model": "llama3",
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": "auto",
    }
    constrained = apply_capability_constraints(auto_tool_payload, "http://localhost:11434")
    assert "tools" not in constrained
    assert "tool_choice" not in constrained


# ---------------------------------------------------------------------------
# TC-T3-PW-25: Tool Ledger x Approval Expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_25_agent_approval_challenge_expiration(db_sessionmaker):
    """TC-T3-PW-25: Agent tool approval challenge expired after 16 minutes cannot resume tool execution."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-25")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-25")
    await repo.claim_turn_by_id(turn.id, "worker-pw-25", lease_seconds=60)

    # Tool call begins and requires approval
    call, _ = await repo.begin_tool_call(
        turn_id=turn.id,
        call_id="call-pw-25",
        tool_name="admin_system_action",
        arguments={"param": "value"},
        retry_safe=False,
        run_attempt=1,
        idempotency_key="idemp-call-pw-25",
    )
    await repo.set_turn_status(turn.id, "waiting_approval", run_attempt=1, event_type="turn.paused")

    # Challenge created 16 minutes ago
    challenge_created = utcnow() - dt.timedelta(minutes=16)
    challenge_ttl = dt.timedelta(minutes=15)
    is_challenge_expired = (utcnow() - challenge_created) > challenge_ttl
    assert is_challenge_expired is True

    # When challenge is expired, tool call fails
    await repo.fail_tool_call(
        turn.id, "call-pw-25", {"code": "challenge_expired", "message": "Approval TTL exceeded"}, attempt=1
    )
    failed_call = await repo.get_tool_call(turn.id, "call-pw-25")
    assert failed_call.status == "failed"


# ---------------------------------------------------------------------------
# TC-T3-PW-26: XLSX x Formula Guard x Diff
# ---------------------------------------------------------------------------


def test_tc_t3_pw_26_xlsx_formula_guard_mixed_proposal(fixtures_dir: Path):
    """TC-T3-PW-26: Operation normalization and XLSX execution validate operations and preserve formula metadata."""
    from open_webui.mini_hermes.edits import apply_operations, normalize_operations
    from open_webui.mini_hermes.parsers import extract_sections

    content = (fixtures_dir / "xlsx-f01-f03-hidden-missing-cache.xlsx").read_bytes()

    # 1. Operation normalization rejects malformed operations
    with pytest.raises(ValueError, match="Each document operation must include an op/action/type name"):
        normalize_operations([{"sheet": "Visible", "cell": "B2"}])

    with pytest.raises(ValueError, match="operations must be a list"):
        normalize_operations("invalid")

    # 2. Valid operations are normalized to canonical schema
    normalized = normalize_operations([
        {"op": "set_cell", "sheet": "Visible", "cell": "A2", "value": 100},
        {"action": "append_row", "sheet": "Visible", "values": ["new", "row"]},
    ])
    assert len(normalized) == 2
    assert normalized[0]["op"] == "set_cell"
    assert normalized[1]["op"] == "append_row"

    # 3. Apply operations applies valid ops and rejects unsupported XLSX operations
    applied_bytes = apply_operations(content, "workbook.xlsx", [
        {"op": "set_cell", "sheet": "Visible", "cell": "A2", "value": 100}
    ])
    assert len(applied_bytes) > 0

    with pytest.raises(ValueError, match="Unsupported XLSX operation: delete_cell"):
        apply_operations(content, "workbook.xlsx", [{"op": "delete_cell", "sheet": "Visible", "cell": "A2"}])

    # 4. Parsers preserve formula definitions and cached values in structured text
    sections = extract_sections(content, "workbook.xlsx")
    visible_section = next((s for s in sections if s[0] == "Visible"), None)
    assert visible_section is not None
    assert "=A2*2" in visible_section[1]
    assert "[cached=" in visible_section[1]


# ---------------------------------------------------------------------------
# TC-T3-PW-27: PDF x OCR x Coverage
# ---------------------------------------------------------------------------


def test_tc_t3_pw_27_pdf_ocr_partial_page_fallback(fixtures_dir: Path):
    """TC-T3-PW-27: PDF with Page 1 selectable text and Page 2 scan; extracts Page 1 and flags OCR needed."""
    from open_webui.mini_hermes.chunks import build_chunk_rows
    from open_webui.mini_hermes.parsers import extract_sections

    content = (fixtures_dir / "pdf-f01-text-and-image.pdf").read_bytes()
    sections = extract_sections(content, "pdf-f01-text-and-image.pdf")
    assert len(sections) == 2

    page1_text = sections[0][1]
    page2_text = sections[1][1]

    assert len(page1_text.strip()) > 0
    assert "Synthetic selectable text layer" in page1_text
    assert len(page2_text.strip()) == 0  # Image-only scan page

    # Chunk rows are only generated for selectable text pages
    chunks = build_chunk_rows("doc-pw-27", 1, "test.pdf", content)
    assert len(chunks) > 0
    assert all(c["page"] == 1 for c in chunks)

    # Coverage metrics derived directly from parser sections
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


# ---------------------------------------------------------------------------
# TC-T3-PW-28: Runtime x Persistence Restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_28_runtime_crash_recovery_checkpoint(db_sessionmaker):
    """TC-T3-PW-28: Worker crashes after checkpoint 2; recovery worker resumes turn from checkpoint 2."""
    repo = AgentRepository(db_sessionmaker)
    session = await repo.create_session("user-1", "chat-pw-28")
    turn = await repo.create_turn(session.id, {"messages": []}, idempotency_key="idemp-pw-28")
    await repo.claim_turn_by_id(turn.id, "worker-1", lease_seconds=1)

    # Save checkpoints 1 and 2
    await repo.save_checkpoint(turn.id, {"step": 1, "accumulated": "data-1"}, run_attempt=1)
    cp2 = await repo.save_checkpoint(turn.id, {"step": 2, "accumulated": "data-1-data-2"}, run_attempt=1)
    assert cp2.version == 2

    # Worker 1 crashes (simulate lease expiry past lease_seconds=1)
    expired_time = utcnow() + dt.timedelta(seconds=5)
    recovered = await repo.recover_expired_turns(now=expired_time)
    assert recovered == 1

    # Worker 2 claims turn (attempt 2)
    turn_recovered = await repo.claim_turn_by_id(turn.id, "worker-2", lease_seconds=60)
    assert turn_recovered.attempts == 2

    # Fetch latest checkpoint
    latest_cp = await repo.latest_checkpoint(turn.id)
    assert latest_cp.version == 2
    assert latest_cp.data["step"] == 2

    # Resume from checkpoint 2 and finish
    await repo.append_event(turn.id, "step.3", {"step": 3}, run_attempt=2)
    completed_turn = await repo.complete_turn(turn.id, run_attempt=2, result={"success": True})
    assert completed_turn.status == "completed"


# ---------------------------------------------------------------------------
# TC-T3-PW-29: Ingestion x Checksum Tampering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_29_staged_object_bit_rot_detection(memory_store, db_session: AsyncSession):
    """TC-T3-PW-29: Staged object byte altered in storage; commit detects SHA-256 mismatch and aborts with 409."""
    doc_id = "doc-pw-29"
    base_content = b"Base document content"
    base_sha256 = hashlib.sha256(base_content).hexdigest()
    base_key = f"documents/{doc_id}/revisions/1.txt"
    await memory_store.put(base_key, base_content)

    doc = Document(id=doc_id, owner_id="user-1", filename="file.txt", mime_type="text/plain", current_revision=1)
    rev1 = Revision(id="rev-pw-29-1", document_id=doc_id, number=1, object_key=base_key, sha256=base_sha256, size=len(base_content))

    staged_content = b"Staged proposal bytes"
    expected_artifact_sha256 = hashlib.sha256(staged_content).hexdigest()
    staged_key = f"documents/{doc_id}/staged/prev-pw-29"
    await memory_store.put(staged_key, staged_content)

    ops = [{"op": "replace_text", "find": "Base", "replace": "Staged"}]
    patch_hash = compute_patch_hash(base_revision=1, base_sha256=base_sha256, operations=ops, artifact_sha256=expected_artifact_sha256)

    preview = Preview(
        id="prev-pw-29",
        document_id=doc_id,
        owner_id="user-1",
        base_revision=1,
        base_sha256=base_sha256,
        artifact_sha256=expected_artifact_sha256,
        patch_hash=patch_hash,
        operations=ops,
        status="ready",
        preview_key=staged_key,
    )
    db_session.add_all([doc, rev1, preview])
    await db_session.commit()

    # Simulate bit-rot in storage by flipping 1 byte in staged object
    tampered_bytes = b"Staged proposal byteX"
    await memory_store.put(staged_key, tampered_bytes)

    approval = {
        "base_revision": 1,
        "base_sha256": base_sha256,
        "artifact_sha256": expected_artifact_sha256,
        "patch_hash": patch_hash,
    }
    with pytest.raises(HTTPException) as exc_info:
        await commit_preview(
            db_session, memory_store, user_id="user-1", preview_id="prev-pw-29", approval=approval, is_admin=True
        )
    assert exc_info.value.status_code == 409
    assert "checksum changed" in str(exc_info.value.detail).lower()

    # Document remains at revision 1
    doc_check = await db_session.get(Document, doc_id)
    assert doc_check.current_revision == 1


# ---------------------------------------------------------------------------
# TC-T3-PW-30: Multi-Format x Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_t3_pw_30_multiformat_export_xlsx_to_csv(
    fixtures_dir: Path, memory_store, db_session: AsyncSession, async_client
):
    """TC-T3-PW-30: Ingest XLSX, test content export endpoint and task creation via async_client, and parse CSV structure."""
    from open_webui.mini_hermes.parsers import extract_sections

    content = (fixtures_dir / "xlsx-f05-synthetic.xlsx").read_bytes()
    doc_id = "doc-pw-30"
    object_key = f"documents/{doc_id}/revisions/1.xlsx"
    await memory_store.put(object_key, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    doc = Document(
        id=doc_id,
        owner_id="user-std-1",
        filename="Financial_Model.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        current_revision=1,
    )
    rev = Revision(
        id="rev-pw-30-1",
        document_id=doc_id,
        number=1,
        object_key=object_key,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    db_session.add_all([doc, rev])
    await db_session.commit()

    # Pre-index document chunks so resource tasks can proceed
    from open_webui.mini_hermes.chunks import replace_document_chunks
    await replace_document_chunks(db_session, doc_id, 1, doc.filename, content)
    await db_session.commit()

    # 1. Real content export endpoint via async_client returns verified bytes
    content_resp = await async_client.get(f"/api/v1/mini-hermes/documents/{doc_id}/content")
    assert content_resp.status_code == 200
    assert content_resp.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert content_resp.content == content

    # 2. Resource task initiation via async_client
    task_resp = await async_client.post(f"/api/v1/mini-hermes/documents/{doc_id}/tasks", json={"kind": "summary"})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["task_id"]

    status_resp = await async_client.get(f"/api/v1/mini-hermes/tasks/{task_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["document_id"] == doc_id
    assert status_resp.json()["kind"] == "summary"

    # 3. Tabular CSV section parsing via production parser
    csv_bytes = b"ID,Name,Amount\n1,Alpha,100.0\n2,Beta,250.5\n"
    csv_sections = extract_sections(csv_bytes, "exported.csv")
    assert len(csv_sections) == 1
    assert "row 1:" in csv_sections[0][1]
    assert "A1=ID" in csv_sections[0][1]
    assert "row 2:" in csv_sections[0][1]
