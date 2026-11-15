"""The 9 Canonical Document Tools for Mini-Hermes V2 (Feature 26).

Implements the async service functions conforming to Phase 0 contracts:
1. inspect_document
2. read_document_range
3. read_spreadsheet_range
4. search_document
5. execute_document_analysis
6. start_document_summary
7. get_document_task
8. export_document
9. propose_document_edit
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import uuid
from typing import Any

from openpyxl.utils import range_boundaries
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.mini_hermes.v2.coverage import CoverageType, build_coverage_proof
from open_webui.mini_hermes.v2.models.auth import MhAccessGrant
from open_webui.mini_hermes.v2.models.canonical import (
    MhDocumentParagraph,
    MhDocumentSection,
    MhMergedRange,
    MhPresentationShape,
    MhPresentationSlide,
    MhWorkbookCell,
    MhWorkbookSheet,
)
from open_webui.mini_hermes.v2.models.core import (
    MhActiveIndex,
    MhChunk,
    MhDocument,
    MhDocumentRevision,
    MhIndexBuild,
    MhIngestionJob,
    MhStagedObject,
)
from open_webui.mini_hermes.v2.models.revisions import (
    MhEditPreview,
    MhIdempotencyRecord,
    MhOutbox,
)
from open_webui.mini_hermes.v2.models.workers import (
    MhAnalysisJob,
    MhDocumentTask,
    MhExportJob,
    MhSummaryTask,
)
from open_webui.mini_hermes.v2.tools.cursor import (
    CursorBuildRetiredError,
    CursorError,
    CursorExpiredError,
    CursorInvalidError,
    CursorScopeMismatchError,
    compute_scope_digest,
    sign_cursor,
    verify_cursor,
)
from open_webui.mini_hermes.v2.tools.pagination import (
    DEFAULT_WIRE_BUDGET_CHARS,
    OutputBudgetTooSmallError,
    measure_wire_characters,
    measure_wire_metrics,
    paginate_records,
)

log = logging.getLogger(__name__)

CONTRACT_VERSION = "2.0-draft"

ALLOWED_EDIT_OPERATIONS: dict[str, set[str]] = {
    "xlsx": {
        "set_cell_value",
        "set_cell_formula",
        "insert_rows",
        "delete_rows",
        "insert_columns",
        "delete_columns",
        "rename_sheet",
        "update_range",
    },
    "docx": {
        "replace_paragraph_text",
        "insert_paragraph",
        "delete_paragraph",
        "update_header_footer",
        "replace_text",
    },
    "pptx": {
        "replace_shape_text",
        "update_slide_title",
        "update_speaker_notes",
    },
}


class ToolError(Exception):
    """Standardized tool exception mapped to Phase 0 error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable

    def to_problem(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": request_id or str(uuid.uuid4()),
            "details": self.details,
        }


DocumentToolError = ToolError


def make_envelope(
    *,
    request_id: str,
    document_id: str | None,
    revision_id: str | None,
    index_build_id: str | None,
    status: str = "complete",
    data: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    """Generates standard Phase 0 response envelope."""
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "document_id": str(document_id) if document_id else None,
        "revision_id": str(revision_id) if revision_id else None,
        "index_build_id": str(index_build_id) if index_build_id else None,
        "status": status,
        "data": data or {},
        "evidence": evidence or [],
        "next_cursor": next_cursor,
    }


async def _check_document_permission(
    session: AsyncSession,
    doc: MhDocument,
    principal_id: str | None,
    tenant_id: str = "default",
    *,
    require_write: bool = False,
) -> None:
    """Verifies current principal and tenant authorization for the given document."""
    if not principal_id or not str(principal_id).strip():
        raise ToolError("FORBIDDEN", "Authenticated principal is required", status_code=403)

    if tenant_id and doc.tenant_id and doc.tenant_id != tenant_id:
        raise ToolError("FORBIDDEN", "Cross-tenant document access denied", status_code=403)

    if doc.owner_id in (principal_id, "system") or principal_id == "admin":
        return

    try:
        grantee_uuid = uuid.UUID(str(principal_id))
    except ValueError:
        grantee_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"mini-hermes.v2.principal:{principal_id}")

    stmt_grant = select(MhAccessGrant).where(
        MhAccessGrant.resource_type.in_(("document", "DOCUMENT")),
        MhAccessGrant.resource_id == doc.id,
        MhAccessGrant.grantee_id == grantee_uuid,
    )
    res_grant = await session.execute(stmt_grant)
    grants = res_grant.scalars().all()

    valid_permissions = {"write", "edit", "owner", "admin"} if require_write else {"read", "view", "write", "edit", "owner", "admin"}
    for grant in grants:
        perm_val = getattr(grant, "permission", None) or getattr(grant, "permission_kind", "")
        if str(perm_val).lower() in valid_permissions:
            return

    raise ToolError(
        "FORBIDDEN",
        f"Principal '{principal_id}' does not have permission for document {doc.id}",
        status_code=403,
    )


async def _find_existing_index_job_details(
    session: AsyncSession,
    doc_id: uuid.UUID,
    rev_id: uuid.UUID,
) -> tuple[str, dict[str, Any]]:
    """Finds any existing ingestion job or index build without creating or retrying a job."""
    stmt_job = (
        select(MhIngestionJob)
        .where(
            MhIngestionJob.document_id == doc_id,
            or_(MhIngestionJob.revision_id == rev_id, MhIngestionJob.revision_id.is_(None)),
        )
        .order_by(MhIngestionJob.created_at.desc())
        .limit(1)
    )
    res_job = await session.execute(stmt_job)
    job = res_job.scalar_one_or_none()

    stmt_build = (
        select(MhIndexBuild)
        .where(
            MhIndexBuild.document_id == doc_id,
            MhIndexBuild.revision_id == rev_id,
        )
        .order_by(MhIndexBuild.created_at.desc())
        .limit(1)
    )
    res_build = await session.execute(stmt_build)
    build = res_build.scalar_one_or_none()

    is_failed = (
        (build is not None and build.build_state.upper() == "FAILED")
        or (job is not None and job.index_state.upper() == "FAILED")
    )
    code = "INDEX_FAILED" if is_failed else "INDEX_NOT_READY"
    details = {
        "job_id": str(job.id) if job is not None else (str(build.id) if build is not None else None),
        "build_id": str(build.id) if build is not None else None,
        "index_state": (
            build.build_state if build is not None else (job.index_state if job is not None else "PENDING")
        ),
        "status": "failed" if is_failed else "pending",
    }
    return code, details


async def _resolve_document_and_revision(
    session: AsyncSession,
    document_id: str,
    revision: int | None = None,
    principal_id: str | None = None,
    tenant_id: str = "default",
    *,
    require_write: bool = False,
    require_ready_index: bool = True,
) -> tuple[MhDocument, MhDocumentRevision, MhIndexBuild | None]:
    """Resolves and authorizes document, requested revision, and verified index build.

    Never creates or retries an index build or ingestion job on read/search/inspect.
    """
    try:
        doc_uuid = uuid.UUID(str(document_id))
    except Exception as exc:
        raise ToolError("INVALID_ARGUMENT", f"Invalid document_id UUID: {document_id}", status_code=400) from exc

    doc = await session.get(MhDocument, doc_uuid)
    if not doc or doc.is_archived:
        raise ToolError("DOCUMENT_NOT_FOUND", f"Document {document_id} was not found", status_code=404)

    await _check_document_permission(
        session,
        doc,
        principal_id,
        tenant_id=tenant_id,
        require_write=require_write,
    )

    if revision is not None:
        stmt_rev = select(MhDocumentRevision).where(
            MhDocumentRevision.document_id == doc.id,
            MhDocumentRevision.revision_no == int(revision),
        )
        res_rev = await session.execute(stmt_rev)
        rev = res_rev.scalar_one_or_none()
        if not rev:
            raise ToolError(
                "DOCUMENT_NOT_FOUND",
                f"Revision {revision} not found for document {document_id}",
                status_code=404,
            )
    else:
        if not doc.current_revision_id:
            raise ToolError("DOCUMENT_NOT_FOUND", "Document has no committed revisions", status_code=404)
        rev = await session.get(MhDocumentRevision, doc.current_revision_id)
        if not rev:
            raise ToolError("DOCUMENT_NOT_FOUND", "Current document revision not found", status_code=404)

    # Resolve active index build for this exact revision
    stmt_active = select(MhActiveIndex).where(
        MhActiveIndex.document_id == doc.id,
        MhActiveIndex.revision_id == rev.id,
    )
    res_active = await session.execute(stmt_active)
    active = res_active.scalar_one_or_none()

    if not active or not active.active_build_id:
        if not require_ready_index:
            return doc, rev, None
        code, details = await _find_existing_index_job_details(session, doc.id, rev.id)
        raise ToolError(
            code,
            "Index build failed" if code == "INDEX_FAILED" else "Index build is still processing or not ready",
            status_code=409,
            details=details,
            retryable=(code == "INDEX_NOT_READY"),
        )

    build = await session.get(MhIndexBuild, active.active_build_id)
    if not build or build.build_state != "READY":
        if not require_ready_index:
            return doc, rev, build
        code, details = await _find_existing_index_job_details(session, doc.id, rev.id)
        raise ToolError(
            code,
            "Index build failed" if code == "INDEX_FAILED" else "Index build is not ready",
            status_code=409,
            details=details,
            retryable=(code == "INDEX_NOT_READY"),
        )

    return doc, rev, build


# =============================================================================
# Tool 1: inspect_document
# =============================================================================


async def inspect_document(
    document_id: str,
    revision: int | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Inspects structural metadata, sheet/section breakdown, and coverage proofs."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    structure_data: dict[str, Any] = {
        "name": doc.name,
        "format_type": doc.format_type,
        "revision_no": rev.revision_no,
        "checksum_sha256": rev.checksum_sha256,
        "byte_size": rev.byte_size,
        "index_status": build.build_state,
        "coverage_proof": build.coverage_proof,
    }

    evidence: list[dict[str, Any]] = [
        {
            "evidence_id": f"ev-{build.id}-inspect",
            "locator": f"{doc.format_type}://{doc.id}/rev/{rev.revision_no}",
            "revision": rev.revision_no,
        }
    ]

    if doc.format_type == "xlsx":
        stmt_sheets = (
            select(MhWorkbookSheet)
            .where(MhWorkbookSheet.index_build_id == build.id)
            .order_by(MhWorkbookSheet.sheet_order.asc())
        )
        res_sheets = await session.execute(stmt_sheets)
        sheets = res_sheets.scalars().all()
        structure_data["sheets"] = [
            {
                "sheet_id": str(s.id),
                "sheet_name": s.sheet_name,
                "sheet_order": s.sheet_order,
                "visibility": s.visibility,
                "max_row": s.max_row,
                "max_col": s.max_col,
            }
            for s in sheets
        ]

    elif doc.format_type == "docx":
        stmt_sec = (
            select(MhDocumentSection)
            .where(MhDocumentSection.index_build_id == build.id)
            .order_by(MhDocumentSection.section_order.asc())
        )
        res_sec = await session.execute(stmt_sec)
        sections = res_sec.scalars().all()
        structure_data["sections"] = [
            {
                "section_id": str(s.id),
                "section_order": s.section_order,
                "header_text": s.header_text,
                "footer_text": s.footer_text,
            }
            for s in sections
        ]

    elif doc.format_type == "pptx":
        stmt_slides = (
            select(MhPresentationSlide)
            .where(MhPresentationSlide.index_build_id == build.id)
            .order_by(MhPresentationSlide.slide_order.asc())
        )
        res_slides = await session.execute(stmt_slides)
        slides = res_slides.scalars().all()
        structure_data["slides"] = [
            {
                "slide_id": str(s.id),
                "slide_order": s.slide_order,
                "slide_number": s.slide_number,
                "title": s.title,
                "layout_name": s.layout_name,
            }
            for s in slides
        ]

    elif doc.format_type == "pdf":
        stmt_chunks = select(func.count(MhChunk.id)).where(MhChunk.index_build_id == build.id)
        res_chunks = await session.execute(stmt_chunks)
        chunk_count = res_chunks.scalar() or 0
        structure_data["pages"] = {"total_pages": chunk_count, "read_only": True}

    return make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        status="complete",
        data=structure_data,
        evidence=evidence,
        next_cursor=None,
    )


# =============================================================================
# Tool 2: read_document_range
# =============================================================================


async def read_document_range(
    document_id: str,
    revision: int | None = None,
    start: int = 0,
    end: int = 100,
    cursor: str | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    max_output_chars: int = DEFAULT_WIRE_BUDGET_CHARS,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Reads sequential document sections/paragraphs within wire budget pagination."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    if int(start) < 0 or int(end) < int(start):
        raise ToolError("INVALID_ARGUMENT", f"Invalid range bounds: start={start}, end={end}", status_code=400)

    scope_dict = {"start": int(start), "end": int(end)}
    expected_scope_sha256 = compute_scope_digest(
        operation="read_document_range",
        document_id=str(doc.id),
        revision=rev.revision_no,
        source_sha256=rev.checksum_sha256,
        build_id=str(build.id),
        scope=scope_dict,
    )

    start_ordinal = 0
    start_fragment: dict[str, Any] | None = None

    if cursor:
        try:
            payload = verify_cursor(
                cursor,
                expected_principal_id=principal_id,
                expected_tenant_id=tenant_id,
                expected_operation="read_document_range",
                expected_document_id=str(doc.id),
                expected_revision=rev.revision_no,
                expected_source_sha256=rev.checksum_sha256,
                expected_build_id=str(build.id),
                expected_scope_sha256=expected_scope_sha256,
            )
            start_ordinal = payload["position"]["item_ordinal"]
            start_fragment = payload["position"]["fragment"]
        except CursorError as exc:
            raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    # Query paragraphs (DOCX), slide shapes (PPTX), or chunks (PDF / fallback)
    unit_type = "chunk"
    if doc.format_type == "docx":
        stmt_docx = (
            select(MhDocumentParagraph, MhDocumentSection.section_order)
            .join(MhDocumentSection, MhDocumentParagraph.section_id == MhDocumentSection.id)
            .where(
                MhDocumentSection.index_build_id == build.id,
                MhDocumentParagraph.paragraph_order >= int(start),
                MhDocumentParagraph.paragraph_order <= int(end),
            )
            .order_by(MhDocumentSection.section_order.asc(), MhDocumentParagraph.paragraph_order.asc())
        )
        res_docx = await session.execute(stmt_docx)
        rows = res_docx.all()
        if rows:
            unit_type = "paragraph"
            items = [
                {
                    "section_order": sec_order,
                    "order": p.paragraph_order,
                    "style": p.style_id,
                    "raw_value": p.raw_text,
                }
                for p, sec_order in rows
            ]
        else:
            items = []
    elif doc.format_type == "pptx":
        stmt_pptx = (
            select(MhPresentationShape, MhPresentationSlide.slide_number)
            .join(MhPresentationSlide, MhPresentationShape.slide_id == MhPresentationSlide.id)
            .where(
                MhPresentationSlide.index_build_id == build.id,
                MhPresentationSlide.slide_order >= int(start),
                MhPresentationSlide.slide_order <= int(end),
            )
            .order_by(MhPresentationSlide.slide_order.asc())
        )
        res_pptx = await session.execute(stmt_pptx)
        rows = res_pptx.all()
        if rows:
            unit_type = "shape"
            items = [
                {
                    "slide_number": slide_no,
                    "shape_name": shp.shape_name,
                    "shape_type": shp.shape_type,
                    "raw_value": shp.text_frame or "",
                }
                for shp, slide_no in rows
            ]
        else:
            items = []
    else:
        items = []

    if not items:
        stmt_chunks = (
            select(MhChunk)
            .where(
                MhChunk.index_build_id == build.id,
                MhChunk.stable_ordinal >= int(start),
                MhChunk.stable_ordinal <= int(end),
            )
            .order_by(MhChunk.stable_ordinal.asc())
        )
        res_chunks = await session.execute(stmt_chunks)
        chunks = res_chunks.scalars().all()
        items = [
            {
                "order": c.stable_ordinal,
                "raw_value": c.raw_text,
            }
            for c in chunks
        ]

    base_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        data={"scope": scope_dict, "total_candidates": len(items)},
        evidence=[
            {
                "evidence_id": f"ev-{build.id}-range-{start}-{end}",
                "locator": f"{doc.format_type}://{doc.id}/range/{start}-{end}",
                "revision": rev.revision_no,
            }
        ],
    )

    def _make_cursor(ord_val: int, frag_val: dict[str, Any] | None) -> str:
        return sign_cursor(
            principal_id=principal_id,
            tenant_id=tenant_id,
            operation="read_document_range",
            document_id=str(doc.id),
            revision=rev.revision_no,
            source_sha256=rev.checksum_sha256,
            build_id=str(build.id),
            scope_sha256=expected_scope_sha256,
            item_ordinal=ord_val,
            fragment=frag_val,
        )

    def _update_envelope(
        env: dict[str, Any],
        whole_intervals: list[list[int]],
        frag_meta: dict[str, Any] | None,
    ) -> None:
        if frag_meta is not None:
            env["data"]["partial_fragment"] = frag_meta
        else:
            env["data"].pop("partial_fragment", None)
        if max_output_chars >= 1200:
            proof = build_coverage_proof(
                coverage_type=CoverageType.RETRIEVAL_COVERAGE,
                document_id=str(doc.id),
                revision=rev.revision_no,
                source_sha256=rev.checksum_sha256,
                index_build_id=str(build.id),
                unit_type=unit_type,
                scope_id=f"range:{start}-{end}",
                enumerated_total=len(items),
                intervals=whole_intervals,
                proof_id=f"proof-{req_id}",
                verified_at="2026-11-14T00:00:00Z",
            )
            env["data"]["retrieval_coverage"] = proof.to_dict()

    try:
        page_env, _ = paginate_records(
            base_envelope=base_env,
            items=items,
            items_key="items",
            make_cursor_fn=_make_cursor,
            update_envelope_fn=_update_envelope,
            max_output_chars=max_output_chars,
            start_ordinal=start_ordinal,
            start_fragment=start_fragment,
            sliceable_field="raw_value",
        )
    except OutputBudgetTooSmallError as exc:
        raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    return page_env


# =============================================================================
# Tool 3: read_spreadsheet_range
# =============================================================================


async def read_spreadsheet_range(
    document_id: str,
    range: str,
    revision: int | None = None,
    sheet_id: str | None = None,
    sheet_name: str | None = None,
    cursor: str | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    max_output_chars: int = DEFAULT_WIRE_BUDGET_CHARS,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Reads a bounded A1 cell range from an XLSX worksheet within wire budget pagination."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    if doc.format_type != "xlsx":
        raise ToolError(
            "UNSUPPORTED_FORMAT",
            f"read_spreadsheet_range is only valid for xlsx, got {doc.format_type}",
            status_code=400,
        )

    # Extract embedded sheet name from 'Sheet1!A1:B10' if sheet_name not explicitly passed
    range_clean = range.strip()
    if "!" in range_clean:
        sheet_prefix, range_clean = range_clean.split("!", 1)
        range_clean = range_clean.strip()
        if not sheet_name and not sheet_id:
            sheet_name = sheet_prefix.strip().strip("'")

    # Resolve sheet
    sheet: MhWorkbookSheet | None = None
    if sheet_id:
        try:
            sheet_uuid = uuid.UUID(str(sheet_id))
            candidate = await session.get(MhWorkbookSheet, sheet_uuid)
            if candidate and candidate.index_build_id == build.id:
                sheet = candidate
        except Exception:
            sheet = None
    elif sheet_name:
        stmt_sheet = select(MhWorkbookSheet).where(
            MhWorkbookSheet.index_build_id == build.id,
            MhWorkbookSheet.sheet_name == sheet_name,
        )
        res_sheet = await session.execute(stmt_sheet)
        sheet = res_sheet.scalar_one_or_none()
    else:
        stmt_first = (
            select(MhWorkbookSheet)
            .where(MhWorkbookSheet.index_build_id == build.id)
            .order_by(MhWorkbookSheet.sheet_order.asc())
            .limit(1)
        )
        res_first = await session.execute(stmt_first)
        sheet = res_first.scalar_one_or_none()

    if not sheet:
        raise ToolError("INVALID_ARGUMENT", "Worksheet not found", status_code=404)

    try:
        min_col, min_row, max_col, max_row = range_boundaries(range_clean)
        if min_col is None or min_row is None:
            raise ValueError("Invalid boundaries")
        if max_col is None:
            max_col = min_col
        if max_row is None:
            max_row = min_row
    except Exception as exc:
        raise ToolError("INVALID_ARGUMENT", f"Invalid A1 range syntax: {range}", status_code=400) from exc

    scope_dict = {
        "sheet_id": str(sheet.id),
        "sheet_name": sheet.sheet_name,
        "range": range_clean,
        "min_row": min_row,
        "min_col": min_col,
        "max_row": max_row,
        "max_col": max_col,
    }
    expected_scope_sha256 = compute_scope_digest(
        operation="read_spreadsheet_range",
        document_id=str(doc.id),
        revision=rev.revision_no,
        source_sha256=rev.checksum_sha256,
        build_id=str(build.id),
        scope=scope_dict,
    )

    start_ordinal = 0
    start_fragment: dict[str, Any] | None = None

    if cursor:
        try:
            payload = verify_cursor(
                cursor,
                expected_principal_id=principal_id,
                expected_tenant_id=tenant_id,
                expected_operation="read_spreadsheet_range",
                expected_document_id=str(doc.id),
                expected_revision=rev.revision_no,
                expected_source_sha256=rev.checksum_sha256,
                expected_build_id=str(build.id),
                expected_scope_sha256=expected_scope_sha256,
            )
            start_ordinal = payload["position"]["item_ordinal"]
            start_fragment = payload["position"]["fragment"]
        except CursorError as exc:
            raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    # Query cells ordered row asc, col asc
    stmt_cells = (
        select(MhWorkbookCell)
        .where(
            MhWorkbookCell.sheet_id == sheet.id,
            MhWorkbookCell.row_idx >= min_row,
            MhWorkbookCell.row_idx <= max_row,
            MhWorkbookCell.col_idx >= min_col,
            MhWorkbookCell.col_idx <= max_col,
        )
        .order_by(MhWorkbookCell.row_idx.asc(), MhWorkbookCell.col_idx.asc())
    )
    res_cells = await session.execute(stmt_cells)
    cells = res_cells.scalars().all()

    def _serialize_cell(c: MhWorkbookCell) -> dict[str, Any]:
        return {
            "coordinate": c.cell_coordinate,
            "row": c.row_idx,
            "col": c.col_idx,
            "data_type": c.data_type,
            "raw_value": c.raw_value or "",
            "formula": c.formula_expression,
            "cached_value": c.cached_value,
            "cached_state": c.cached_value_state,
            "number_format": c.number_format,
        }

    base_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        data={
            "sheet_id": str(sheet.id),
            "sheet_name": sheet.sheet_name,
            "range": range_clean,
            "total_cells": len(cells),
        },
        evidence=[
            {
                "evidence_id": f"ev-{build.id}-{sheet.sheet_name}-{range_clean}",
                "locator": f"xlsx://{doc.id}/sheets/{sheet.sheet_name}/cells/{range_clean}",
                "revision": rev.revision_no,
            }
        ],
    )

    def _make_cursor(ord_val: int, frag_val: dict[str, Any] | None) -> str:
        return sign_cursor(
            principal_id=principal_id,
            tenant_id=tenant_id,
            operation="read_spreadsheet_range",
            document_id=str(doc.id),
            revision=rev.revision_no,
            source_sha256=rev.checksum_sha256,
            build_id=str(build.id),
            scope_sha256=expected_scope_sha256,
            item_ordinal=ord_val,
            fragment=frag_val,
        )

    def _update_envelope(
        env: dict[str, Any],
        whole_intervals: list[list[int]],
        frag_meta: dict[str, Any] | None,
    ) -> None:
        if frag_meta is not None:
            env["data"]["partial_fragment"] = frag_meta
        else:
            env["data"].pop("partial_fragment", None)
        if max_output_chars >= 1200:
            proof = build_coverage_proof(
                coverage_type=CoverageType.RETRIEVAL_COVERAGE,
                document_id=str(doc.id),
                revision=rev.revision_no,
                source_sha256=rev.checksum_sha256,
                index_build_id=str(build.id),
                unit_type="cell",
                scope_id=f"{sheet.sheet_name}!{range_clean}",
                enumerated_total=len(cells),
                intervals=whole_intervals,
                proof_id=f"proof-{req_id}",
                verified_at="2026-11-14T00:00:00Z",
            )
            env["data"]["retrieval_coverage"] = proof.to_dict()

    try:
        page_env, _ = paginate_records(
            base_envelope=base_env,
            items=cells,
            items_key="cells",
            serialize_item_fn=_serialize_cell,
            make_cursor_fn=_make_cursor,
            update_envelope_fn=_update_envelope,
            max_output_chars=max_output_chars,
            start_ordinal=start_ordinal,
            start_fragment=start_fragment,
            sliceable_field="raw_value",
        )
    except OutputBudgetTooSmallError as exc:
        raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    return page_env


# =============================================================================
# Tool 4: search_document
# =============================================================================


async def search_document(
    document_id: str,
    query: str,
    revision: int | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
    cursor: str | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    max_output_chars: int = DEFAULT_WIRE_BUDGET_CHARS,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Searches indexed document units matching query and filters within wire budget."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    query_clean = query.strip()
    if not query_clean:
        raise ToolError("INVALID_ARGUMENT", "Search query cannot be empty", status_code=400)

    scope_dict = {
        "query": query_clean.lower(),
        "filters": filters or {},
        "limit": int(limit),
    }
    expected_scope_sha256 = compute_scope_digest(
        operation="search_document",
        document_id=str(doc.id),
        revision=rev.revision_no,
        source_sha256=rev.checksum_sha256,
        build_id=str(build.id),
        scope=scope_dict,
    )

    start_ordinal = 0
    start_fragment: dict[str, Any] | None = None

    if cursor:
        try:
            payload = verify_cursor(
                cursor,
                expected_principal_id=principal_id,
                expected_tenant_id=tenant_id,
                expected_operation="search_document",
                expected_document_id=str(doc.id),
                expected_revision=rev.revision_no,
                expected_source_sha256=rev.checksum_sha256,
                expected_build_id=str(build.id),
                expected_scope_sha256=expected_scope_sha256,
            )
            start_ordinal = payload["position"]["item_ordinal"]
            start_fragment = payload["position"]["fragment"]
        except CursorError as exc:
            raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    search_term = f"%{query_clean}%"
    candidates: list[dict[str, Any]] = []

    if doc.format_type == "xlsx":
        sheet_filter = (filters or {}).get("sheet_name")
        stmt_cells = (
            select(MhWorkbookCell, MhWorkbookSheet.sheet_name)
            .join(MhWorkbookSheet, MhWorkbookCell.sheet_id == MhWorkbookSheet.id)
            .where(
                MhWorkbookSheet.index_build_id == build.id,
                or_(
                    MhWorkbookCell.raw_value.ilike(search_term),
                    MhWorkbookCell.cached_value.ilike(search_term),
                    MhWorkbookCell.formula_expression.ilike(search_term),
                ),
            )
        )
        if sheet_filter:
            stmt_cells = stmt_cells.where(MhWorkbookSheet.sheet_name == sheet_filter)
        stmt_cells = stmt_cells.order_by(
            MhWorkbookSheet.sheet_order.asc(),
            MhWorkbookCell.row_idx.asc(),
            MhWorkbookCell.col_idx.asc(),
        ).limit(limit)
        res_cells = await session.execute(stmt_cells)
        for c, sname in res_cells.all():
            candidates.append(
                {
                    "locator": f"{sname}!{c.cell_coordinate}",
                    "unit_type": "cell",
                    "raw_value": c.raw_value or c.cached_value or c.formula_expression or "",
                    "score": 1.0,
                }
            )
    elif doc.format_type == "docx":
        stmt_paras = (
            select(MhDocumentParagraph, MhDocumentSection.section_order)
            .join(MhDocumentSection, MhDocumentParagraph.section_id == MhDocumentSection.id)
            .where(
                MhDocumentSection.index_build_id == build.id,
                MhDocumentParagraph.raw_text.ilike(search_term),
            )
            .order_by(MhDocumentSection.section_order.asc(), MhDocumentParagraph.paragraph_order.asc())
            .limit(limit)
        )
        res_paras = await session.execute(stmt_paras)
        for p, sec_order in res_paras.all():
            candidates.append(
                {
                    "locator": f"section://{sec_order}/paragraph/{p.paragraph_order}",
                    "unit_type": "paragraph",
                    "raw_value": p.raw_text,
                    "score": 1.0,
                }
            )

    if not candidates:
        stmt_chunks = (
            select(MhChunk)
            .where(
                MhChunk.index_build_id == build.id,
                MhChunk.raw_text.ilike(search_term),
            )
            .order_by(MhChunk.stable_ordinal.asc())
            .limit(limit)
        )
        res_chunks = await session.execute(stmt_chunks)
        for chk in res_chunks.scalars().all():
            candidates.append(
                {
                    "locator": f"chunk://{chk.stable_ordinal}",
                    "unit_type": "chunk",
                    "raw_value": chk.raw_text,
                    "score": 1.0,
                }
            )

    base_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        data={"query": query_clean, "total_matches": len(candidates)},
        evidence=[
            {
                "evidence_id": f"ev-{build.id}-search-{hashlib.sha256(query_clean.encode()).hexdigest()[:8]}",
                "locator": f"search://{doc.id}?q={query_clean}",
                "revision": rev.revision_no,
            }
        ],
    )

    def _make_cursor(ord_val: int, frag_val: dict[str, Any] | None) -> str:
        return sign_cursor(
            principal_id=principal_id,
            tenant_id=tenant_id,
            operation="search_document",
            document_id=str(doc.id),
            revision=rev.revision_no,
            source_sha256=rev.checksum_sha256,
            build_id=str(build.id),
            scope_sha256=expected_scope_sha256,
            item_ordinal=ord_val,
            fragment=frag_val,
        )

    try:
        page_env, _ = paginate_records(
            base_envelope=base_env,
            items=candidates,
            items_key="matches",
            make_cursor_fn=_make_cursor,
            max_output_chars=max_output_chars,
            start_ordinal=start_ordinal,
            start_fragment=start_fragment,
            sliceable_field="raw_value",
        )
    except OutputBudgetTooSmallError as exc:
        raise ToolError(exc.code, exc.message, status_code=exc.status_code) from exc

    return page_env


# =============================================================================
# Tool 5: execute_document_analysis
# =============================================================================


async def execute_document_analysis(
    document_id: str,
    goal: str,
    analysis_spec: dict[str, Any],
    idempotency_key: str,
    revision: int | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Creates an idempotent long-running document analysis task."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    if not goal or not str(goal).strip():
        raise ToolError("INVALID_ARGUMENT", "Analysis goal is required", status_code=400)
    if not isinstance(analysis_spec, dict):
        raise ToolError("INVALID_ARGUMENT", "analysis_spec must be an object", status_code=400)

    fingerprint = hashlib.sha256(
        json.dumps(
            {"doc_id": str(doc.id), "rev": rev.revision_no, "goal": goal, "spec": analysis_spec},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    stmt_idem = select(MhIdempotencyRecord).where(
        MhIdempotencyRecord.principal_id == principal_id,
        MhIdempotencyRecord.operation_kind == "execute_document_analysis",
        MhIdempotencyRecord.idempotency_key == idempotency_key,
    )
    res_idem = await session.execute(stmt_idem)
    existing_idem = res_idem.scalar_one_or_none()

    if existing_idem:
        if existing_idem.request_fingerprint != fingerprint:
            raise ToolError("CONFLICT", "Idempotency key reused with different parameters", status_code=409)
        return existing_idem.cached_response_json

    task = MhDocumentTask(
        document_id=doc.id,
        revision_id=rev.id,
        task_type="analysis",
        status="pending",
        progress_percent=0,
        created_by=principal_id,
    )
    session.add(task)
    await session.flush()

    job = MhAnalysisJob(
        task_id=task.id,
        document_id=doc.id,
        goal=goal,
        specification_json=analysis_spec,
        status="pending",
    )
    session.add(job)

    result_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        status="pending",
        data={
            "task_id": str(task.id),
            "job_id": str(job.id),
            "goal": goal,
            "input_scope": analysis_spec.get("scope", "full"),
            "status": "pending",
        },
        evidence=[
            {
                "evidence_id": f"ev-analysis-{task.id}",
                "locator": f"task://analysis/{task.id}",
                "revision": rev.revision_no,
            }
        ],
    )

    idem_rec = MhIdempotencyRecord(
        principal_id=principal_id,
        operation_kind="execute_document_analysis",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        response_status_code=200,
        cached_response_json=result_env,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24),
    )
    session.add(idem_rec)
    await session.commit()

    return result_env


# =============================================================================
# Tool 6: start_document_summary
# =============================================================================


async def start_document_summary(
    document_id: str,
    summary_spec: dict[str, Any],
    idempotency_key: str,
    revision: int | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Dispatches an asynchronous summary task."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    if not isinstance(summary_spec, dict):
        raise ToolError("INVALID_ARGUMENT", "summary_spec must be an object", status_code=400)

    fingerprint = hashlib.sha256(
        json.dumps(
            {"doc_id": str(doc.id), "rev": rev.revision_no, "spec": summary_spec},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    stmt_idem = select(MhIdempotencyRecord).where(
        MhIdempotencyRecord.principal_id == principal_id,
        MhIdempotencyRecord.operation_kind == "start_document_summary",
        MhIdempotencyRecord.idempotency_key == idempotency_key,
    )
    res_idem = await session.execute(stmt_idem)
    existing_idem = res_idem.scalar_one_or_none()

    if existing_idem:
        if existing_idem.request_fingerprint != fingerprint:
            raise ToolError("CONFLICT", "Idempotency key reused with different summary spec", status_code=409)
        return existing_idem.cached_response_json

    task = MhDocumentTask(
        document_id=doc.id,
        revision_id=rev.id,
        task_type="summary",
        status="pending",
        progress_percent=0,
        created_by=principal_id,
    )
    session.add(task)
    await session.flush()

    summary_task = MhSummaryTask(
        task_id=task.id,
        document_id=doc.id,
        scope_json=summary_spec,
        status="pending",
    )
    session.add(summary_task)

    result_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        status="pending",
        data={
            "task_id": str(task.id),
            "summary_task_id": str(summary_task.id),
            "input_scope": summary_spec.get("scope", "full"),
            "status": "pending",
        },
        evidence=[
            {
                "evidence_id": f"ev-summary-{task.id}",
                "locator": f"task://summary/{task.id}",
                "revision": rev.revision_no,
            }
        ],
    )

    idem_rec = MhIdempotencyRecord(
        principal_id=principal_id,
        operation_kind="start_document_summary",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        response_status_code=200,
        cached_response_json=result_env,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24),
    )
    session.add(idem_rec)
    await session.commit()

    return result_env


# =============================================================================
# Tool 7: get_document_task
# =============================================================================


async def get_document_task(
    task_id: str,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Retrieves current execution status, progress, and result of a document task."""
    req_id = request_id or str(uuid.uuid4())
    try:
        task_uuid = uuid.UUID(str(task_id))
    except Exception as exc:
        raise ToolError("INVALID_ARGUMENT", f"Invalid task_id: {task_id}", status_code=400) from exc

    task = await session.get(MhDocumentTask, task_uuid)
    if not task:
        raise ToolError("TASK_NOT_FOUND", f"Document task {task_id} not found", status_code=404)

    doc = await session.get(MhDocument, task.document_id)
    if not doc or doc.is_archived:
        raise ToolError("DOCUMENT_NOT_FOUND", "Associated document not found", status_code=404)

    await _check_document_permission(session, doc, principal_id, tenant_id=tenant_id)

    job_details: dict[str, Any] = {}
    if task.task_type == "analysis":
        stmt_job = select(MhAnalysisJob).where(MhAnalysisJob.task_id == task.id)
        res_job = await session.execute(stmt_job)
        job = res_job.scalar_one_or_none()
        if job:
            job_details = {
                "goal": job.goal,
                "verified_outcome": job.verified_outcome_json,
                "evidence_proof": job.evidence_proof_json,
            }
    elif task.task_type == "summary":
        stmt_sum = select(MhSummaryTask).where(MhSummaryTask.task_id == task.id)
        res_sum = await session.execute(stmt_sum)
        st = res_sum.scalar_one_or_none()
        if st:
            job_details = {
                "structured_summary": st.structured_summary,
                "citation_anchors": st.citation_anchors_json,
            }
    elif task.task_type == "export":
        exp = await session.get(MhExportJob, task.id)
        if exp is None:
            stmt_exp = (
                select(MhExportJob)
                .where(
                    MhExportJob.document_id == task.document_id,
                    MhExportJob.revision_id == task.revision_id,
                )
                .order_by(MhExportJob.created_at.desc())
                .limit(1)
            )
            res_exp = await session.execute(stmt_exp)
            exp = res_exp.scalar_one_or_none()
        if exp:
            job_details = {
                "target_format": exp.target_format,
                "exported_object_key": exp.exported_object_key,
                "checksum_sha256": exp.checksum_sha256,
                "byte_size": exp.byte_size,
            }

    return make_envelope(
        request_id=req_id,
        document_id=str(task.document_id),
        revision_id=str(task.revision_id) if task.revision_id else None,
        index_build_id=None,
        status=task.status,
        data={
            "task_id": str(task.id),
            "task_type": task.task_type,
            "status": task.status,
            "progress_percent": task.progress_percent,
            "error_message": task.error_message,
            "details": job_details,
        },
    )


# =============================================================================
# Tool 8: export_document
# =============================================================================


async def export_document(
    document_id: str,
    format: str,
    idempotency_key: str,
    revision: int | None = None,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Dispatches a standalone document export task."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session, document_id, revision, principal_id, tenant_id
    )
    assert build is not None

    fmt = format.lower().strip()
    if fmt not in ("pdf", "xlsx", "docx", "csv"):
        raise ToolError("UNSUPPORTED_FORMAT", f"Export format '{format}' is not supported", status_code=400)

    fingerprint = hashlib.sha256(f"{doc.id}:{rev.revision_no}:{fmt}".encode("utf-8")).hexdigest()

    stmt_idem = select(MhIdempotencyRecord).where(
        MhIdempotencyRecord.principal_id == principal_id,
        MhIdempotencyRecord.operation_kind == "export_document",
        MhIdempotencyRecord.idempotency_key == idempotency_key,
    )
    res_idem = await session.execute(stmt_idem)
    existing_idem = res_idem.scalar_one_or_none()

    if existing_idem:
        if existing_idem.request_fingerprint != fingerprint:
            raise ToolError("CONFLICT", "Idempotency key reused with different export options", status_code=409)
        return existing_idem.cached_response_json

    task = MhDocumentTask(
        document_id=doc.id,
        revision_id=rev.id,
        task_type="export",
        status="pending",
        progress_percent=0,
        created_by=principal_id,
    )
    session.add(task)
    await session.flush()

    export_job = MhExportJob(
        id=task.id,
        document_id=doc.id,
        revision_id=rev.id,
        target_format=fmt,
        status="pending",
    )
    session.add(export_job)

    result_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        status="pending",
        data={
            "task_id": str(task.id),
            "export_job_id": str(export_job.id),
            "format": fmt,
            "status": "pending",
        },
        evidence=[
            {
                "evidence_id": f"ev-export-{task.id}",
                "locator": f"task://export/{task.id}",
                "revision": rev.revision_no,
            }
        ],
    )

    idem_rec = MhIdempotencyRecord(
        principal_id=principal_id,
        operation_kind="export_document",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        response_status_code=200,
        cached_response_json=result_env,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24),
    )
    session.add(idem_rec)
    await session.commit()

    return result_env


# =============================================================================
# Tool 9: propose_document_edit
# =============================================================================


async def propose_document_edit(
    document_id: str,
    base_revision: int,
    operations: list[dict[str, Any]],
    idempotency_key: str,
    *,
    session: AsyncSession,
    principal_id: str,
    tenant_id: str = "default",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Proposes a staged document edit preview with 15-minute TTL. NEVER COMMITS."""
    req_id = request_id or str(uuid.uuid4())
    doc, rev, build = await _resolve_document_and_revision(
        session,
        document_id,
        base_revision,
        principal_id,
        tenant_id,
        require_write=True,
    )
    assert build is not None

    # Invariant: PDF is strictly read-only
    if doc.format_type == "pdf":
        raise ToolError(
            "UNSUPPORTED_EDIT_OPERATION",
            "PDF documents are strictly read-only and cannot be edited",
            status_code=422,
        )

    # Invariant: base_revision must match current revision
    if doc.current_revision_no != int(base_revision):
        raise ToolError(
            "REVISION_CONFLICT",
            f"base_revision {base_revision} does not match current committed revision {doc.current_revision_no}",
            status_code=409,
        )

    if not operations or not isinstance(operations, list):
        raise ToolError("INVALID_ARGUMENT", "Operations must be a non-empty list", status_code=400)

    allowed_ops = ALLOWED_EDIT_OPERATIONS.get(doc.format_type, set())
    for op_item in operations:
        if not isinstance(op_item, dict) or not op_item.get("op"):
            raise ToolError("INVALID_ARGUMENT", "Each operation must be an object with an 'op' field", status_code=400)
        if allowed_ops and op_item["op"] not in allowed_ops:
            raise ToolError(
                "UNSUPPORTED_EDIT_OPERATION",
                f"Operation '{op_item['op']}' is not allowed for format '{doc.format_type}'",
                status_code=422,
            )

    # Canonical patch hash computation (D03b profile)
    simulated_artifact_bytes = json.dumps(
        {"base_sha256": rev.checksum_sha256, "operations": operations},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    simulated_artifact_sha256 = hashlib.sha256(simulated_artifact_bytes).hexdigest()

    patch_payload = {
        "artifact_sha256": simulated_artifact_sha256.lower(),
        "base_revision": int(base_revision),
        "base_sha256": rev.checksum_sha256.lower(),
        "document_id": str(doc.id),
        "operations": operations,
    }
    canonical_patch_bytes = json.dumps(
        patch_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    canonical_patch_hash = hashlib.sha256(b"mhv2:patch:v1\n" + canonical_patch_bytes).hexdigest()

    stmt_idem = select(MhIdempotencyRecord).where(
        MhIdempotencyRecord.principal_id == principal_id,
        MhIdempotencyRecord.operation_kind == "propose_document_edit",
        MhIdempotencyRecord.idempotency_key == idempotency_key,
    )
    res_idem = await session.execute(stmt_idem)
    existing_idem = res_idem.scalar_one_or_none()
    if existing_idem:
        if existing_idem.request_fingerprint != canonical_patch_hash:
            raise ToolError("CONFLICT", "Idempotency key reused with different edit operations", status_code=409)
        return existing_idem.cached_response_json

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(seconds=900)

    staged = MhStagedObject(
        checksum_sha256=None,
        staged_object_key=f"v2/staged/{doc.id}/{canonical_patch_hash}",
        document_id=doc.id,
        principal_id=principal_id,
        byte_size=len(simulated_artifact_bytes),
        stage_status="STAGED",
        verified_at=now,
        expires_at=expires_at,
    )
    session.add(staged)
    await session.flush()

    diff_summary = f"Proposed {len(operations)} edit operation(s) on revision {base_revision}"

    preview = MhEditPreview(
        document_id=doc.id,
        base_revision_id=rev.id,
        base_revision_no=rev.revision_no,
        base_sha256=rev.checksum_sha256,
        artifact_sha256=simulated_artifact_sha256,
        staged_object_id=staged.id,
        canonical_patch_hash=canonical_patch_hash,
        operations_json=operations,
        diff_summary=diff_summary,
        status="ready",
        expires_at=expires_at,
    )
    session.add(preview)
    await session.flush()

    result_env = make_envelope(
        request_id=req_id,
        document_id=str(doc.id),
        revision_id=str(rev.id),
        index_build_id=str(build.id),
        status="ready",
        data={
            "preview_id": str(preview.id),
            "staged_object_id": str(staged.id),
            "base_revision": int(base_revision),
            "patch_hash": canonical_patch_hash,
            "expires_at": expires_at.isoformat(),
            "diff_summary": diff_summary,
            "operations_count": len(operations),
            "status": "ready",
        },
        evidence=[
            {
                "evidence_id": f"ev-preview-{preview.id}",
                "locator": f"preview://{preview.id}",
                "revision": rev.revision_no,
            }
        ],
    )

    idem_rec = MhIdempotencyRecord(
        principal_id=principal_id,
        operation_kind="propose_document_edit",
        idempotency_key=idempotency_key,
        request_fingerprint=canonical_patch_hash,
        response_status_code=200,
        cached_response_json=result_env,
        expires_at=expires_at,
    )
    session.add(idem_rec)
    await session.commit()

    return result_env
