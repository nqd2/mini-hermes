"""FastAPI routes for Mini-Hermes Document API V2."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from openpyxl.utils import column_index_from_string, range_boundaries
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from open_webui.utils.auth import get_verified_user

from open_webui.mini_hermes.v2.canonical.spreadsheet import COORD_REGEX
from open_webui.mini_hermes.v2.db import create_v2_async_engine, create_v2_sessionmaker
from open_webui.mini_hermes.v2.indexing import IndexLifecycleManager, IndexNotReadyError
from open_webui.mini_hermes.v2.ingestion import IngestionError, IngestionPipeline
from open_webui.mini_hermes.v2.models import (
    MhActiveIndex,
    MhDocument,
    MhDocumentParagraph,
    MhDocumentRevision,
    MhDocumentSection,
    MhEditPreview,
    MhIndexBuild,
    MhMergedRange,
    MhPresentationShape,
    MhPresentationSlide,
    MhStagedObject,
    MhWorkbookCell,
    MhWorkbookSheet,
    utcnow,
)
from open_webui.mini_hermes.v2.revision import (
    RevisionError,
    RevisionService,
    verify_current_document_permission,
    verify_staged_blob_before_commit,
)
from open_webui.mini_hermes.v2.storage import V2ObjectStore

log = logging.getLogger(__name__)

router = APIRouter()

CURSOR_SECRET = os.getenv("MH_CURSOR_SECRET", "mini-hermes-v2-cursor-signing-secret-key-32b")
KID = "k1"

_v2_engine = None
_v2_sessionmaker = None


def get_v2_session_factory() -> async_sessionmaker[AsyncSession]:
    global _v2_engine, _v2_sessionmaker
    if _v2_sessionmaker is None:
        _v2_engine = create_v2_async_engine()
        _v2_sessionmaker = create_v2_sessionmaker(_v2_engine)
    return _v2_sessionmaker


async def get_v2_db() -> AsyncSession:
    factory = get_v2_session_factory()
    async with factory() as session:
        yield session


async def get_current_principal(user=Depends(get_verified_user)) -> str:
    """Use the same authenticated, verified identity as the product APIs."""
    return str(user.id)


# DTO Schemas
class ReadSpreadsheetRangeRequest(BaseModel):
    sheet_id: str | None = None
    sheet_name: str | None = None
    range: str
    cursor: str | None = None
    max_output_chars: int = 5952


class ReadRangeRequest(BaseModel):
    start: int = 0
    end: int = 100
    cursor: str | None = None
    max_output_chars: int = 5952


class SearchDocumentRequest(BaseModel):
    query: str
    limit: int = 20


class IngestDocumentPayload(BaseModel):
    filename: str
    content_type: str
    idempotency_key: str
    content_b64: str | None = None
    receipt_id: str | None = None
    expected_sha256: str | None = None
    target_folder_id: str | None = None


def sign_cursor(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")
    msg = f"mhv2c1.{KID}.{payload_b64}".encode("utf-8")
    tag = hmac.new(CURSOR_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"mhv2c1.{KID}.{payload_b64}.{tag}"


def verify_cursor(token: str, expected_scope: str, expected_build_id: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "mhv2c1":
        raise HTTPException(
            status_code=400,
            detail={"code": "CURSOR_INVALID", "message": "Invalid cursor token format"},
        )
    kid, payload_b64, tag = parts[1], parts[2], parts[3]
    msg = f"mhv2c1.{kid}.{payload_b64}".encode("utf-8")
    expected_tag = hmac.new(CURSOR_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tag, expected_tag):
        raise HTTPException(
            status_code=400,
            detail={"code": "CURSOR_INVALID", "message": "Cursor signature verification failed"},
        )

    pad = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + pad)
        payload = json.loads(payload_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "CURSOR_INVALID", "message": f"Malformed cursor payload: {exc}"},
        ) from exc

    # Check expiration (15-minute TTL)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if payload.get("exp", 0) < now_ts:
        raise HTTPException(
            status_code=410,
            detail={"code": "CURSOR_EXPIRED", "message": "Cursor token has expired (15-minute TTL)"},
        )

    # Check scope
    if payload.get("scope") != expected_scope:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CURSOR_SCOPE_MISMATCH",
                "message": "Cursor scope does not match requested resource or range",
            },
        )

    # Check build ID (prevent cross-revision / retired build leakage)
    if payload.get("build") != expected_build_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CURSOR_BUILD_RETIRED",
                "message": "Cursor references a retired or superseded index build",
            },
        )

    return payload


@router.get("/documents/{document_id}/inspect")
async def inspect_document_endpoint(
    document_id: str,
    revision: int | None = None,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Inspects document metadata, active revision, structure, and coverage proof."""
    request_id = str(uuid.uuid4())
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id UUID")

    doc = await db.get(MhDocument, doc_uuid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        await verify_current_document_permission(db, doc, principal, "read")
    except RevisionError as exc:
        _raise_revision_http_error(exc)

    # Resolve revision
    if revision is not None:
        stmt_rev = select(MhDocumentRevision).where(
            MhDocumentRevision.document_id == doc_uuid,
            MhDocumentRevision.revision_no == revision,
        )
        res_rev = await db.execute(stmt_rev)
        rev = res_rev.scalar_one_or_none()
        if not rev:
            raise HTTPException(status_code=404, detail=f"Revision {revision} not found")
    else:
        if not doc.current_revision_id:
            raise HTTPException(status_code=404, detail="Document has no committed revisions")
        rev = await db.get(MhDocumentRevision, doc.current_revision_id)
        if not rev:
            raise HTTPException(status_code=404, detail="Current revision not found")

    # Resolve active index
    factory = get_v2_session_factory()
    idx_mgr = IndexLifecycleManager(factory)
    try:
        active, build = await idx_mgr.get_active_index_for_revision(rev.id)
    except IndexNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INDEX_NOT_READY",
                "message": f"Index build for revision {rev.revision_no} is still processing.",
            },
        )

    # Gather structural metadata depending on format
    structure_data: dict[str, Any] = {
        "name": doc.name,
        "format_type": doc.format_type,
        "revision_no": rev.revision_no,
        "checksum_sha256": rev.checksum_sha256,
        "byte_size": rev.byte_size,
    }

    if doc.format_type == "xlsx":
        stmt_sheets = (
            select(MhWorkbookSheet)
            .where(MhWorkbookSheet.index_build_id == build.id)
            .order_by(MhWorkbookSheet.sheet_order.asc())
        )
        res_sheets = await db.execute(stmt_sheets)
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
        res_sec = await db.execute(stmt_sec)
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
        res_slides = await db.execute(stmt_slides)
        slides = res_slides.scalars().all()
        structure_data["slides"] = [
            {
                "slide_id": str(s.id),
                "slide_order": s.slide_order,
                "title": s.title,
                "layout_name": s.layout_name,
            }
            for s in slides
        ]

    return {
        "contract_version": "2.0-draft",
        "request_id": request_id,
        "document_id": str(doc.id),
        "revision_id": str(rev.id),
        "index_build_id": str(build.id),
        "status": "complete",
        "data": structure_data,
        "evidence": [
            {
                "evidence_id": f"ev-{build.id}-summary",
                "locator": f"doc:{doc.id}:rev:{rev.revision_no}",
                "revision": rev.revision_no,
            }
        ],
    }


@router.post("/documents/{document_id}/read-spreadsheet-range")
async def read_spreadsheet_range_endpoint(
    document_id: str,
    body: ReadSpreadsheetRangeRequest,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Reads spreadsheet range enforcing wire JSON pagination budget and signed cursors."""
    request_id = str(uuid.uuid4())
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id UUID")

    doc = await db.get(MhDocument, doc_uuid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        await verify_current_document_permission(db, doc, principal, "read")
    except RevisionError as exc:
        _raise_revision_http_error(exc)
    if doc.format_type != "xlsx":
        raise HTTPException(status_code=422, detail="Document format is not XLSX")
    if not doc.current_revision_id:
        raise HTTPException(status_code=404, detail="Document has no committed revisions")

    rev = await db.get(MhDocumentRevision, doc.current_revision_id)
    factory = get_v2_session_factory()
    idx_mgr = IndexLifecycleManager(factory)
    try:
        active, build = await idx_mgr.get_active_index_for_revision(rev.id)
    except IndexNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={"code": "INDEX_NOT_READY", "message": "Active index build not ready"},
        )

    # Resolve Sheet
    sheet = None
    if body.sheet_id:
        try:
            s_uuid = uuid.UUID(body.sheet_id)
            sheet = await db.get(MhWorkbookSheet, s_uuid)
            if sheet is not None and sheet.index_build_id != build.id:
                raise HTTPException(status_code=404, detail="Worksheet not found in this document")
        except ValueError:
            pass
    if not sheet and body.sheet_name:
        stmt_s = select(MhWorkbookSheet).where(
            MhWorkbookSheet.index_build_id == build.id,
            MhWorkbookSheet.sheet_name == body.sheet_name,
        )
        res_s = await db.execute(stmt_s)
        sheet = res_s.scalar_one_or_none()

    if not sheet:
        # Default to first sheet
        stmt_first = (
            select(MhWorkbookSheet)
            .where(MhWorkbookSheet.index_build_id == build.id)
            .order_by(MhWorkbookSheet.sheet_order.asc())
            .limit(1)
        )
        res_first = await db.execute(stmt_first)
        sheet = res_first.scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="No worksheets found for document")

    # Scope definition for cursor security
    scope_str = f"{doc.id}:{sheet.id}:{body.range}"
    scope_sha256 = hashlib.sha256(scope_str.encode("utf-8")).hexdigest()

    offset = 0
    if body.cursor:
        cursor_payload = verify_cursor(
            token=body.cursor,
            expected_scope=scope_sha256,
            expected_build_id=str(build.id),
        )
        offset = cursor_payload.get("offset", 0)

    # Parse range boundaries
    range_str = body.range.strip()
    try:
        min_col, min_row, max_col, max_row = range_boundaries(range_str)
        if min_col is None or min_row is None or max_col is None or max_row is None:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid cell range expression: '{range_str}'")

    # Fetch cells in range
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
    res_cells = await db.execute(stmt_cells)
    all_cells = res_cells.scalars().all()

    # Slice starting at offset
    remaining_cells = all_cells[offset:]

    # Check output budget limit
    # First: measure minimal envelope size without any cells
    minimal_envelope = {
        "contract_version": "2.0-draft",
        "request_id": request_id,
        "document_id": str(doc.id),
        "revision_id": str(rev.id),
        "index_build_id": str(build.id),
        "status": "complete",
        "data": {
            "sheet_name": sheet.sheet_name,
            "range": body.range,
            "cells": [],
            "total_in_page": 0,
        },
        "evidence": [
            {
                "evidence_id": f"ev-{build.id}-{sheet.sheet_name}-{body.range}",
                "locator": f"sheet:{sheet.sheet_name}!{body.range}",
                "revision": rev.revision_no,
            }
        ],
        "next_cursor": None,
    }
    min_wire_len = len(json.dumps(minimal_envelope, ensure_ascii=False))
    if min_wire_len > body.max_output_chars:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OUTPUT_BUDGET_TOO_SMALL",
                "message": f"Minimal envelope ({min_wire_len} chars) exceeds output budget limit of {body.max_output_chars} characters",
                "retryable": False,
                "request_id": request_id,
            },
        )

    # Greedily pack cells while keeping serialized wire JSON body within budget
    packed_cells: list[dict[str, Any]] = []
    has_more = False

    # Pre-calculate dummy next_cursor length for size estimation
    dummy_cursor = sign_cursor(
        {
            "doc": str(doc.id),
            "rev": str(rev.id),
            "build": str(build.id),
            "scope": scope_sha256,
            "offset": 999999,
            "exp": int(datetime.now(timezone.utc).timestamp()) + 900,
        }
    )
    dummy_cursor_len = len(dummy_cursor)

    for idx, c in enumerate(remaining_cells):
        cell_dict = {
            "coordinate": c.cell_coordinate,
            "row": c.row_idx,
            "col": c.col_idx,
            "data_type": c.data_type,
            "raw_value": c.raw_value,
            "formula": c.formula_expression,
            "cached_value": c.cached_value,
            "cached_state": c.cached_value_state,
            "number_format": c.number_format,
        }
        trial_cells = packed_cells + [cell_dict]
        # Tentative envelope check
        trial_has_more = (idx + 1) < len(remaining_cells)
        minimal_envelope["data"]["cells"] = trial_cells
        minimal_envelope["data"]["total_in_page"] = len(trial_cells)
        minimal_envelope["next_cursor"] = dummy_cursor if trial_has_more else None

        trial_wire = json.dumps(minimal_envelope, ensure_ascii=False)
        if len(trial_wire) <= body.max_output_chars:
            packed_cells.append(cell_dict)
        else:
            has_more = True
            break

    # Determine next cursor
    next_cursor = None
    if has_more or (offset + len(packed_cells)) < len(all_cells):
        next_offset = offset + len(packed_cells)
        exp_ts = int(datetime.now(timezone.utc).timestamp()) + 900
        next_cursor = sign_cursor(
            {
                "doc": str(doc.id),
                "rev": str(rev.id),
                "build": str(build.id),
                "scope": scope_sha256,
                "offset": next_offset,
                "exp": exp_ts,
            }
        )

    minimal_envelope["data"]["cells"] = packed_cells
    minimal_envelope["data"]["total_in_page"] = len(packed_cells)
    minimal_envelope["next_cursor"] = next_cursor

    # Final assertion of wire budget
    final_wire = json.dumps(minimal_envelope, ensure_ascii=False)
    if len(final_wire) > body.max_output_chars:
        # Fallback reduction if cursor formatting difference pushed over
        if packed_cells:
            packed_cells.pop()
            next_offset = offset + len(packed_cells)
            exp_ts = int(datetime.now(timezone.utc).timestamp()) + 900
            minimal_envelope["next_cursor"] = sign_cursor(
                {
                    "doc": str(doc.id),
                    "rev": str(rev.id),
                    "build": str(build.id),
                    "scope": scope_sha256,
                    "offset": next_offset,
                    "exp": exp_ts,
                }
            )
            minimal_envelope["data"]["cells"] = packed_cells
            minimal_envelope["data"]["total_in_page"] = len(packed_cells)

    return minimal_envelope


@router.post("/documents/{document_id}/read-range")
async def read_range_endpoint(
    document_id: str,
    body: ReadRangeRequest,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Reads range of paragraphs/sections for text documents."""
    request_id = str(uuid.uuid4())
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id UUID")

    doc = await db.get(MhDocument, doc_uuid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        await verify_current_document_permission(db, doc, principal, "read")
    except RevisionError as exc:
        _raise_revision_http_error(exc)
    if not doc.current_revision_id:
        raise HTTPException(status_code=404, detail="Document has no committed revisions")

    rev = await db.get(MhDocumentRevision, doc.current_revision_id)
    factory = get_v2_session_factory()
    idx_mgr = IndexLifecycleManager(factory)
    try:
        active, build = await idx_mgr.get_active_index_for_revision(rev.id)
    except IndexNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={"code": "INDEX_NOT_READY", "message": "Index build not ready"},
        )

    # Query paragraphs
    stmt_paras = (
        select(MhDocumentParagraph)
        .join(MhDocumentSection, MhDocumentParagraph.section_id == MhDocumentSection.id)
        .where(
            MhDocumentSection.index_build_id == build.id,
            MhDocumentParagraph.paragraph_order >= body.start,
            MhDocumentParagraph.paragraph_order <= body.end,
        )
        .order_by(MhDocumentParagraph.paragraph_order.asc())
    )
    res = await db.execute(stmt_paras)
    paras = res.scalars().all()

    return {
        "contract_version": "2.0-draft",
        "request_id": request_id,
        "document_id": str(doc.id),
        "revision_id": str(rev.id),
        "index_build_id": str(build.id),
        "status": "complete",
        "data": {
            "start": body.start,
            "end": body.end,
            "paragraphs": [
                {
                    "order": p.paragraph_order,
                    "style_id": p.style_id,
                    "text": p.raw_text,
                    "runs": p.runs_json,
                }
                for p in paras
            ],
        },
        "evidence": [
            {
                "evidence_id": f"ev-{build.id}-para-{body.start}-{body.end}",
                "locator": f"para:{body.start}-{body.end}",
                "revision": rev.revision_no,
            }
        ],
        "next_cursor": None,
    }


@router.post("/documents/{document_id}/search")
async def search_document_endpoint(
    document_id: str,
    body: SearchDocumentRequest,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Searches content across cells, paragraphs, or slide shapes."""
    request_id = str(uuid.uuid4())
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id UUID")

    doc = await db.get(MhDocument, doc_uuid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        await verify_current_document_permission(db, doc, principal, "read")
    except RevisionError as exc:
        _raise_revision_http_error(exc)
    if not doc.current_revision_id:
        raise HTTPException(status_code=404, detail="Document has no committed revisions")

    rev = await db.get(MhDocumentRevision, doc.current_revision_id)
    factory = get_v2_session_factory()
    idx_mgr = IndexLifecycleManager(factory)
    try:
        active, build = await idx_mgr.get_active_index_for_revision(rev.id)
    except IndexNotReadyError:
        raise HTTPException(
            status_code=409,
            detail={"code": "INDEX_NOT_READY", "message": "Index build not ready"},
        )

    matches: list[dict[str, Any]] = []
    term = f"%{body.query}%"

    if doc.format_type == "xlsx":
        stmt = (
            select(MhWorkbookCell)
            .where(
                MhWorkbookCell.index_build_id == build.id,
                (MhWorkbookCell.raw_value.ilike(term))
                | (MhWorkbookCell.formula_expression.ilike(term))
                | (MhWorkbookCell.cached_value.ilike(term)),
            )
            .limit(body.limit)
        )
        res = await db.execute(stmt)
        for c in res.scalars():
            matches.append(
                {
                    "type": "cell",
                    "coordinate": c.cell_coordinate,
                    "value": c.raw_value or c.cached_value or c.formula_expression,
                }
            )

    elif doc.format_type == "docx":
        stmt = (
            select(MhDocumentParagraph)
            .join(MhDocumentSection, MhDocumentParagraph.section_id == MhDocumentSection.id)
            .where(
                MhDocumentSection.index_build_id == build.id,
                MhDocumentParagraph.raw_text.ilike(term),
            )
            .limit(body.limit)
        )
        res = await db.execute(stmt)
        for p in res.scalars():
            matches.append(
                {
                    "type": "paragraph",
                    "order": p.paragraph_order,
                    "text": p.raw_text,
                }
            )

    elif doc.format_type == "pptx":
        stmt = (
            select(MhPresentationShape)
            .join(MhPresentationSlide, MhPresentationShape.slide_id == MhPresentationSlide.id)
            .where(
                MhPresentationSlide.index_build_id == build.id,
                MhPresentationShape.text_frame.ilike(term),
            )
            .limit(body.limit)
        )
        res = await db.execute(stmt)
        for s in res.scalars():
            matches.append(
                {
                    "type": "shape",
                    "shape_id": str(s.id),
                    "shape_name": s.shape_name,
                    "text": s.text_frame,
                }
            )

    return {
        "contract_version": "2.0-draft",
        "request_id": request_id,
        "document_id": str(doc.id),
        "revision_id": str(rev.id),
        "index_build_id": str(build.id),
        "status": "complete",
        "data": {
            "query": body.query,
            "total_matches": len(matches),
            "matches": matches,
        },
        "evidence": [
            {
                "evidence_id": f"ev-{build.id}-search-{hashlib.sha256(body.query.encode()).hexdigest()[:8]}",
                "locator": f"search:{body.query}",
                "revision": rev.revision_no,
            }
        ],
    }


@router.post("/documents/ingest")
async def ingest_document_endpoint(
    request: Request,
    payload: IngestDocumentPayload | None = None,
    file: UploadFile | None = File(None),
    filename: str | None = Form(None),
    idempotency_key: str | None = Form(None),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Ingests file via direct base64 JSON payload, multipart upload, or upload receipt."""
    factory = get_v2_session_factory()
    pipeline = IngestionPipeline(session_factory=factory)

    raw_bytes: bytes | None = None
    target_filename: str = ""
    target_content_type: str = "application/octet-stream"
    target_idempotency_key: str = ""
    receipt_uuid: uuid.UUID | None = None
    expected_sha256: str | None = None
    target_folder_uuid: uuid.UUID | None = None

    if file is not None:
        raw_bytes = await file.read()
        target_filename = filename or file.filename or "uploaded_file"
        target_content_type = file.content_type or "application/octet-stream"
        target_idempotency_key = idempotency_key or str(uuid.uuid4())
    elif payload is not None:
        target_filename = payload.filename
        target_content_type = payload.content_type
        target_idempotency_key = payload.idempotency_key
        expected_sha256 = payload.expected_sha256
        if payload.content_b64:
            raw_bytes = base64.b64decode(payload.content_b64)
        if payload.receipt_id:
            try:
                receipt_uuid = uuid.UUID(payload.receipt_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid receipt_id UUID")
        if payload.target_folder_id:
            try:
                target_folder_uuid = uuid.UUID(payload.target_folder_id)
            except ValueError:
                pass
    else:
        raise HTTPException(status_code=400, detail="No ingestion payload or file provided")

    try:
        res = await pipeline.start_ingestion(
            principal_id=principal,
            filename=target_filename,
            content_type=target_content_type,
            idempotency_key=target_idempotency_key,
            data=raw_bytes,
            receipt_id=receipt_uuid,
            expected_sha256=expected_sha256,
            target_folder_id=target_folder_uuid,
        )
        return res
    except IngestionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        )


@router.get("/documents/{document_id}/ingestion-status/{job_id}")
async def ingestion_status_endpoint(
    document_id: str,
    job_id: str,
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Returns the current processing status of an ingestion job."""
    factory = get_v2_session_factory()
    pipeline = IngestionPipeline(session_factory=factory)
    try:
        j_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id UUID")

    try:
        return await pipeline.get_ingestion_status(job_id=j_uuid, principal_id=principal)
    except IngestionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        )


# ==============================================================================
# Phase 5 & 6: Revision Service Endpoints (Previews, Approval, Commit, Rollback)
# ==============================================================================

_v2_store_instance: V2ObjectStore | None = None


def get_v2_store() -> V2ObjectStore:
    """Provides the V2ObjectStore instance for Revision Service operations."""
    global _v2_store_instance
    if _v2_store_instance is None:
        _v2_store_instance = V2ObjectStore()
    return _v2_store_instance


def _get_now_fn(request: Request) -> Any:
    """Allows deterministic clock injection in tests via app.state.now_fn or falls back to utcnow."""
    custom_now = getattr(request.app.state, "now_fn", None)
    return custom_now if callable(custom_now) else utcnow


def _raise_revision_http_error(exc: RevisionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


class CreatePreviewRequest(BaseModel):
    base_revision: int | None = None
    operations: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str | None = None


class CommitPreviewRequest(BaseModel):
    model_config = {"extra": "allow"}

    base_revision: int
    patch_hash: str
    idempotency_key: str
    base_sha256: str | None = None
    artifact_sha256: str | None = None


class RejectPreviewRequest(BaseModel):
    reason: str | None = None


class RollbackDocumentRequest(BaseModel):
    target_revision: int
    expected_current_revision: int | None = None
    reason: str = Field(default="Compensating rollback to historical revision", min_length=1)
    idempotency_key: str | None = None


@router.post("/documents/{document_id}/previews", status_code=201)
@router.post("/v2/documents/{document_id}/previews", status_code=201)
@router.post("/documents/{document_id}/propose-edit", status_code=201)
@router.post("/v2/documents/{document_id}/propose-edit", status_code=201)
async def create_preview_endpoint(
    document_id: str,
    body: CreatePreviewRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Creates an in-memory edit preview, stages & verifies the blob, and binds a 15-minute approval window."""
    effective_idem_key = body.idempotency_key or idempotency_key
    try:
        res = await RevisionService.create_preview(
            session=db,
            store=store,
            principal_id=principal,
            document_id=document_id,
            operations=body.operations,
            base_revision=body.base_revision,
            idempotency_key=effective_idem_key,
            now_fn=_get_now_fn(request),
        )
        if res.get("idempotent_replay"):
            response.status_code = 200
        return res
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.get("/documents/{document_id}/previews")
@router.get("/v2/documents/{document_id}/previews")
async def list_document_previews_endpoint(
    document_id: str,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Lists edit previews for a document after verifying read permission."""
    try:
        doc = await RevisionService._get_document(db, document_id)
        await verify_current_document_permission(db, doc, principal, "read")
        stmt = (
            select(MhEditPreview)
            .where(MhEditPreview.document_id == doc.id)
            .order_by(MhEditPreview.created_at.desc())
        )
        previews = list((await db.execute(stmt)).scalars().all())
        return {
            "document_id": str(doc.id),
            "previews": [
                {
                    "preview_id": str(p.id),
                    "document_id": str(p.document_id),
                    "base_revision": p.base_revision_no,
                    "patch_hash": p.canonical_patch_hash,
                    "diff_summary": p.diff_summary,
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "expires_at": p.expires_at.isoformat() if p.expires_at else None,
                }
                for p in previews
            ],
        }
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.get("/previews/{preview_id}")
@router.get("/v2/previews/{preview_id}")
async def get_preview_endpoint(
    preview_id: str,
    request: Request,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Retrieves an edit preview and its 15-minute approval metadata."""
    try:
        return await RevisionService.get_preview(
            session=db,
            principal_id=principal,
            preview_id=preview_id,
            now_fn=_get_now_fn(request),
        )
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.get("/previews/{preview_id}/content")
@router.get("/v2/previews/{preview_id}/content")
async def get_preview_content_endpoint(
    preview_id: str,
    request: Request,
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> Response:
    """Streams the verified staged artifact bytes for a preview."""
    try:
        prev_info = await RevisionService.get_preview(
            session=db,
            principal_id=principal,
            preview_id=preview_id,
            now_fn=_get_now_fn(request),
        )
        doc = await RevisionService._get_document(db, prev_info["document_id"])
        staged_obj = (
            await db.get(MhStagedObject, uuid.UUID(prev_info["staged_object_id"]))
            if prev_info.get("staged_object_id")
            else None
        )
        raw_bytes = verify_staged_blob_before_commit(
            store,
            staged_obj,
            prev_info["artifact_sha256"],
        )
        return Response(content=raw_bytes, media_type=doc.mime_type)
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.post("/previews/{preview_id}/retry")
@router.post("/v2/previews/{preview_id}/retry")
async def retry_preview_endpoint(
    preview_id: str,
    request: Request,
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Retries/re-verifies a preview WITHOUT extending its 15-minute expiration window."""
    try:
        return await RevisionService.retry_preview(
            session=db,
            store=store,
            principal_id=principal,
            preview_id=preview_id,
            now_fn=_get_now_fn(request),
        )
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.post("/previews/{preview_id}/reject")
@router.post("/v2/previews/{preview_id}/reject")
async def reject_preview_endpoint(
    preview_id: str,
    request: Request,
    body: RejectPreviewRequest | None = None,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Explicitly rejects a pending preview."""
    try:
        return await RevisionService.reject_preview(
            session=db,
            principal_id=principal,
            preview_id=preview_id,
            reason=body.reason if body else None,
            now_fn=_get_now_fn(request),
        )
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.post("/previews/{preview_id}/commit")
@router.post("/v2/previews/{preview_id}/commit")
async def commit_preview_endpoint(
    preview_id: str,
    body: CommitPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Atomically approves and commits a preview in one database transaction."""
    raw_payload = body.model_dump()
    try:
        return await RevisionService.commit_preview(
            session=db,
            store=store,
            principal_id=principal,
            preview_id=preview_id,
            base_revision=body.base_revision,
            patch_hash=body.patch_hash,
            idempotency_key=body.idempotency_key,
            base_sha256=body.base_sha256,
            artifact_sha256=body.artifact_sha256,
            raw_approval_payload=raw_payload,
            now_fn=_get_now_fn(request),
        )
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.post("/documents/{document_id}/rollback")
@router.post("/v2/documents/{document_id}/rollback")
async def rollback_document_endpoint(
    document_id: str,
    body: RollbackDocumentRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Creates a compensating revision pointing to a verified historical revision's bytes."""
    effective_idem_key = body.idempotency_key or idempotency_key
    try:
        return await RevisionService.rollback_document(
            session=db,
            store=store,
            principal_id=principal,
            document_id=document_id,
            target_revision=body.target_revision,
            expected_current_revision=body.expected_current_revision,
            reason=body.reason,
            idempotency_key=effective_idem_key,
            now_fn=_get_now_fn(request),
        )
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.get("/documents/{document_id}/revisions")
@router.get("/v2/documents/{document_id}/revisions")
async def list_document_revisions_endpoint(
    document_id: str,
    db: AsyncSession = Depends(get_v2_db),
    principal: str = Depends(get_current_principal),
) -> dict[str, Any]:
    """Lists committed revisions for a document (never exposing uncommitted staged objects)."""
    try:
        revs = await RevisionService.list_revisions(
            session=db,
            principal_id=principal,
            document_id=document_id,
        )
        return {
            "document_id": document_id,
            "revisions": revs,
        }
    except RevisionError as exc:
        _raise_revision_http_error(exc)


@router.get("/documents/{document_id}/content")
@router.get("/v2/documents/{document_id}/content")
async def get_document_content_endpoint(
    document_id: str,
    revision: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_v2_db),
    store: V2ObjectStore = Depends(get_v2_store),
    principal: str = Depends(get_current_principal),
) -> Response:
    """Streams and verifies committed document bytes (failing closed on corruption)."""
    try:
        content, doc, _ = await RevisionService.read_committed_content(
            session=db,
            store=store,
            principal_id=principal,
            document_id=document_id,
            revision_no=revision,
        )
        return Response(content=content, media_type=doc.mime_type)
    except RevisionError as exc:
        _raise_revision_http_error(exc)
