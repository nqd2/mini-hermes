"""Revision Service: Single-Transaction Atomic Publication & Compensating Rollback (M4.4).

Implements the authoritative V2 Revision Service for Mini-Hermes:
1. `create_preview`: Authorizes principal, enforces format capability matrix allowlist (and PDF HTTP 422),
   applies operations in memory, stages & verifies immutable blob (`v2/blobs/{sha256}`), computes
   canonical `patch_hash`, and creates 15-minute `MhEditPreview` + `MhDocumentApproval`.
2. `retry_preview`: Re-verifies a pending preview while strictly preserving the original
   `expires_at = created_at + 15 minutes` (never extending expiration).
3. `commit_preview`: Executes single-transaction atomic publication:
   - Rechecks current resource permission via Auth Service / V2 RBAC (even on idempotent replay).
   - Enforces idempotency via `MhIdempotencyRecord` and D03b request fingerprint (`mhv2:request:v1`).
   - Re-verifies staged blob bytes and canonical patch hash prior to commit.
   - In ONE database transaction: consumes `MhDocumentApproval`, transitions `MhStagedObject` to
     `COMMITTED`, inserts `MhDocumentRevision` (`COMMITTED`), advances `MhDocument.current_revision_id`
     and `current_revision_no`, and inserts `MhOutbox` event (`document.revision.committed`).
4. `rollback_document`: Creates a new forward compensating revision (`N + 1`) pointing to a verified
   historical revision's bytes, records `MhCompensatingRevision`, updates `MhDocument`, and emits
   an outbox event while preserving all historical revisions.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.mini_hermes.v2.models import (
    ApprovalStatus,
    MhCompensatingRevision,
    MhDocument,
    MhDocumentApproval,
    MhDocumentRevision,
    MhEditPreview,
    MhIdempotencyRecord,
    MhObjectRegistry,
    MhStagedObject,
    PreviewStatus,
    PublicationState,
    utcnow,
)
from open_webui.mini_hermes.v2.outbox import insert_outbox_event
from open_webui.mini_hermes.v2.parsers.pdf import FormatEditUnsupportedError, PdfParser
from open_webui.mini_hermes.v2.revision.approval import (
    ApprovalExpiredError,
    ApprovalInvalidError,
    PermissionDeniedError,
    RevisionConflictError,
    compute_fixed_approval_expiry,
    ensure_utc,
    validate_approval_for_commit,
    verify_current_document_permission,
)
from open_webui.mini_hermes.v2.revision.preview import (
    InvalidPatchPayloadError,
    UnsupportedEditOperationError,
    apply_operations_in_memory,
    compute_canonical_patch_hash,
    compute_request_fingerprint,
)
from open_webui.mini_hermes.v2.revision.state_machine import (
    RevisionError,
    RevisionIntegrityFailureError,
    StageVerificationError,
    cleanup_abandoned_staged_objects,
    get_committed_revisions,
    stage_and_verify_artifact,
    transition_revision_state,
    validate_stage_transition,
    verify_committed_revision_bytes,
    verify_staged_blob_before_commit,
)
from open_webui.mini_hermes.v2.storage import V2ObjectStore

IDEMPOTENCY_TTL = timedelta(hours=24)


class DocumentNotFoundError(RevisionError):
    """Raised when a target document does not exist (HTTP 404)."""

    def __init__(self, document_id: uuid.UUID | str) -> None:
        super().__init__(
            f"Document '{document_id}' not found.",
            code="NOT_FOUND",
            status_code=404,
            retryable=False,
            details={"document_id": str(document_id)},
        )


class PreviewNotFoundError(RevisionError):
    """Raised when a target edit preview does not exist (HTTP 404)."""

    def __init__(self, preview_id: uuid.UUID | str) -> None:
        super().__init__(
            f"Preview '{preview_id}' not found.",
            code="NOT_FOUND",
            status_code=404,
            retryable=False,
            details={"preview_id": str(preview_id)},
        )


class IdempotencyConflictError(RevisionError):
    """Raised when an idempotency_key is reused with a different request payload (HTTP 409)."""

    def __init__(self, idempotency_key: str, operation: str) -> None:
        super().__init__(
            f"Idempotency key '{idempotency_key}' was already used with a different request payload for '{operation}'.",
            code="IDEMPOTENCY_CONFLICT",
            status_code=409,
            retryable=False,
            details={
                "legacy_code": "idempotency_key_reused",
                "idempotency_key": idempotency_key,
                "operation": operation,
            },
        )


def _coerce_uuid(val: uuid.UUID | str, field_name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except ValueError as exc:
        raise RevisionError(
            f"Invalid UUID for {field_name}: '{val}'.",
            code="INVALID_ARGUMENT",
            status_code=422,
        ) from exc


def _principal_to_uuid(principal_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(principal_id).strip())
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"mh.user.{str(principal_id).strip()}")


def _serialize_preview(
    preview: MhEditPreview,
    document: MhDocument,
    *,
    staged_object_key: str | None = None,
    structured_diffs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    created_iso = ensure_utc(preview.created_at).isoformat()
    expires_iso = ensure_utc(preview.expires_at).isoformat()
    ops = preview.operations_json if isinstance(preview.operations_json, list) else []
    return {
        "preview_id": str(preview.id),
        "id": str(preview.id),
        "document_id": str(preview.document_id),
        "document_name": document.name,
        "format_type": document.format_type,
        "base_revision_id": str(preview.base_revision_id),
        "base_revision": preview.base_revision_no,
        "base_revision_no": preview.base_revision_no,
        "base_sha256": preview.base_sha256,
        "artifact_sha256": preview.artifact_sha256,
        "staged_object_id": str(preview.staged_object_id) if preview.staged_object_id else None,
        "staged_object_key": staged_object_key or f"v2/blobs/{preview.artifact_sha256}",
        "patch_hash": preview.canonical_patch_hash,
        "canonical_patch_hash": preview.canonical_patch_hash,
        "operations": ops,
        "diff_summary": preview.diff_summary,
        "structured_diffs": structured_diffs or [],
        "status": preview.status,
        "created_at": created_iso,
        "expires_at": expires_iso,
    }


def _serialize_revision(rev: MhDocumentRevision) -> dict[str, Any]:
    return {
        "revision_id": str(rev.id),
        "id": str(rev.id),
        "document_id": str(rev.document_id),
        "revision_no": rev.revision_no,
        "revision": rev.revision_no,
        "object_key": rev.object_key,
        "checksum_sha256": rev.checksum_sha256,
        "byte_size": rev.byte_size,
        "source_metadata": rev.source_metadata or {},
        "commit_status": rev.commit_status,
        "committed_by": rev.committed_by,
        "committed_at": ensure_utc(rev.committed_at).isoformat() if rev.committed_at else None,
    }


class RevisionService:
    """Authoritative V2 Revision Service for preview, approval, atomic commit, and rollback."""

    @staticmethod
    async def _get_document(session: AsyncSession, document_id: uuid.UUID | str) -> MhDocument:
        doc_uuid = _coerce_uuid(document_id, "document_id")
        stmt = (
            select(MhDocument)
            .where(MhDocument.id == doc_uuid, MhDocument.is_archived.is_(False))
            .execution_options(populate_existing=True)
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc is None:
            raise DocumentNotFoundError(doc_uuid)
        return doc

    @staticmethod
    async def _get_current_revision(
        session: AsyncSession,
        document: MhDocument,
    ) -> MhDocumentRevision:
        rev: MhDocumentRevision | None = None
        if document.current_revision_id is not None:
            rev = await session.get(MhDocumentRevision, document.current_revision_id)
        if rev is None:
            stmt = select(MhDocumentRevision).where(
                MhDocumentRevision.document_id == document.id,
                MhDocumentRevision.revision_no == document.current_revision_no,
            )
            rev = (await session.execute(stmt)).scalar_one_or_none()
        if rev is None:
            raise RevisionError(
                f"Current revision {document.current_revision_no} for document '{document.id}' not found.",
                code="NOT_FOUND",
                status_code=404,
            )
        return rev

    @classmethod
    async def create_preview(
        cls,
        session: AsyncSession,
        store: V2ObjectStore,
        *,
        principal_id: str,
        document_id: uuid.UUID | str,
        operations: list[dict[str, Any]],
        base_revision: int | None = None,
        idempotency_key: str | None = None,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Creates an in-memory edit preview, stages & verifies the resulting artifact, and binds a 15m approval."""
        doc = await cls._get_document(session, document_id)

        # 1. Verify current write permission
        await verify_current_document_permission(session, doc, principal_id, "edit")

        # 2. Enforce PDF Read-Only Invariant unconditionally before any other processing
        if doc.format_type.lower().strip() == "pdf":
            try:
                PdfParser.propose_edit(operations=operations)
            except FormatEditUnsupportedError as exc:
                raise RevisionError(
                    exc.message,
                    code="format_edit_unsupported",
                    status_code=422,
                    retryable=False,
                    details={"format": "pdf", "canonical_code": "UNSUPPORTED_EDIT_OPERATION"},
                ) from exc

        target_base_rev = doc.current_revision_no if base_revision is None else int(base_revision)

        # 3. Check idempotency record if idempotency_key is provided
        clean_idem_key = str(idempotency_key).strip() if idempotency_key else None
        req_fingerprint: str | None = None
        if clean_idem_key:
            req_fingerprint = compute_request_fingerprint(
                principal_id=principal_id,
                operation="create_preview",
                request={
                    "base_revision": target_base_rev,
                    "document_id": str(doc.id),
                    "operations": operations,
                },
            )
            stmt_idem = select(MhIdempotencyRecord).where(
                MhIdempotencyRecord.principal_id == principal_id,
                MhIdempotencyRecord.operation_kind == "create_preview",
                MhIdempotencyRecord.idempotency_key == clean_idem_key,
            )
            existing_idem = (await session.execute(stmt_idem)).scalar_one_or_none()
            if existing_idem is not None:
                if existing_idem.request_fingerprint != req_fingerprint:
                    raise IdempotencyConflictError(clean_idem_key, "create_preview")
                return {
                    **existing_idem.cached_response_json,
                    "idempotent_replay": True,
                }

        # 4. Verify base_revision matches current committed revision
        if target_base_rev != doc.current_revision_no:
            raise RevisionConflictError(
                doc.id,
                expected_base=target_base_rev,
                current_revision=doc.current_revision_no,
            )

        current_rev = await cls._get_current_revision(session, doc)
        base_bytes = verify_committed_revision_bytes(store, current_rev)

        # 5. Apply operations in memory (enforcing format capability matrix allowlist)
        staged_bytes, diff_summary, structured_diffs = apply_operations_in_memory(
            format_type=doc.format_type,
            base_bytes=base_bytes,
            operations=operations,
        )

        now = ensure_utc(now_fn())
        fixed_expires_at = compute_fixed_approval_expiry(now)

        # 6. Stage and verify immutable checksum-addressed artifact (`STAGED` -> `VERIFIED`)
        staged_obj, scoped_key, artifact_sha256 = await stage_and_verify_artifact(
            session,
            store,
            staged_bytes,
            document_id=doc.id,
            principal_id=principal_id,
            now_fn=lambda: now,
        )

        # 7. Compute canonical patch_hash per D03b
        canonical_patch_hash = compute_canonical_patch_hash(
            document_id=str(doc.id),
            base_revision=current_rev.revision_no,
            base_sha256=current_rev.checksum_sha256,
            operations=operations,
            artifact_sha256=artifact_sha256,
        )

        # 8. Persist MhEditPreview and MhDocumentApproval bound to 15-minute window
        preview_id = uuid.uuid4()
        preview = MhEditPreview(
            id=preview_id,
            document_id=doc.id,
            base_revision_id=current_rev.id,
            base_revision_no=current_rev.revision_no,
            base_sha256=current_rev.checksum_sha256,
            artifact_sha256=artifact_sha256,
            staged_object_id=staged_obj.id,
            canonical_patch_hash=canonical_patch_hash,
            operations_json=operations,
            diff_summary=diff_summary,
            status=PreviewStatus.READY.value,
            expires_at=fixed_expires_at,
            created_at=now,
        )
        session.add(preview)

        approval = MhDocumentApproval(
            id=uuid.uuid4(),
            preview_id=preview_id,
            document_id=doc.id,
            base_revision_no=current_rev.revision_no,
            canonical_patch_hash=canonical_patch_hash,
            approver_user_id=str(principal_id),
            status=ApprovalStatus.PENDING.value,
            consumed_at=None,
            expires_at=fixed_expires_at,
            created_at=now,
        )
        session.add(approval)

        response_payload = _serialize_preview(
            preview,
            doc,
            staged_object_key=scoped_key,
            structured_diffs=structured_diffs,
        )

        if clean_idem_key and req_fingerprint:
            idem_record = MhIdempotencyRecord(
                id=uuid.uuid4(),
                principal_id=principal_id,
                operation_kind="create_preview",
                idempotency_key=clean_idem_key,
                request_fingerprint=req_fingerprint,
                response_status_code=201,
                cached_response_json=response_payload,
                expires_at=now + IDEMPOTENCY_TTL,
                created_at=now,
            )
            session.add(idem_record)

        await session.commit()
        return response_payload

    @classmethod
    async def get_preview(
        cls,
        session: AsyncSession,
        *,
        principal_id: str,
        preview_id: uuid.UUID | str,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Retrieves an edit preview after verifying current read permission on its document."""
        prev_uuid = _coerce_uuid(preview_id, "preview_id")
        preview = await session.get(MhEditPreview, prev_uuid)
        if preview is None:
            raise PreviewNotFoundError(prev_uuid)

        doc = await cls._get_document(session, preview.document_id)
        await verify_current_document_permission(session, doc, principal_id, "read")

        now = ensure_utc(now_fn())
        fixed_expires_at = compute_fixed_approval_expiry(preview.created_at)
        if preview.status == PreviewStatus.READY.value and now >= min(
            fixed_expires_at, ensure_utc(preview.expires_at)
        ):
            preview.status = PreviewStatus.EXPIRED.value
            await session.commit()

        return _serialize_preview(preview, doc)

    @classmethod
    async def retry_preview(
        cls,
        session: AsyncSession,
        store: V2ObjectStore,
        *,
        principal_id: str,
        preview_id: uuid.UUID | str,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Retries/re-verifies an existing preview WITHOUT extending its 15-minute expiration window."""
        prev_uuid = _coerce_uuid(preview_id, "preview_id")
        preview = await session.get(MhEditPreview, prev_uuid)
        if preview is None:
            raise PreviewNotFoundError(prev_uuid)

        doc = await cls._get_document(session, preview.document_id)
        await verify_current_document_permission(session, doc, principal_id, "edit")

        now = ensure_utc(now_fn())
        fixed_expires_at = compute_fixed_approval_expiry(preview.created_at)
        current_expires_at = min(fixed_expires_at, ensure_utc(preview.expires_at))

        # Strictly enforce that retries NEVER extend expiration
        preview.expires_at = current_expires_at

        stmt_app = select(MhDocumentApproval).where(MhDocumentApproval.preview_id == preview.id)
        approval = (await session.execute(stmt_app)).scalar_one_or_none()
        if approval is not None:
            approval.expires_at = min(fixed_expires_at, ensure_utc(approval.expires_at))

        if now >= current_expires_at or preview.status == PreviewStatus.EXPIRED.value:
            preview.status = PreviewStatus.EXPIRED.value
            if approval is not None and approval.status == ApprovalStatus.PENDING.value:
                approval.status = ApprovalStatus.EXPIRED.value
            await session.commit()
            raise ApprovalExpiredError(preview.id, current_expires_at)

        staged_obj = (
            await session.get(MhStagedObject, preview.staged_object_id)
            if preview.staged_object_id
            else None
        )
        verify_staged_blob_before_commit(store, staged_obj, preview.artifact_sha256)
        await session.commit()
        return _serialize_preview(
            preview,
            doc,
            staged_object_key=staged_obj.staged_object_key if staged_obj else None,
        )

    @classmethod
    async def reject_preview(
        cls,
        session: AsyncSession,
        *,
        principal_id: str,
        preview_id: uuid.UUID | str,
        reason: str | None = None,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Explicitly rejects a pending preview so it cannot be committed."""
        prev_uuid = _coerce_uuid(preview_id, "preview_id")
        preview = await session.get(MhEditPreview, prev_uuid)
        if preview is None:
            raise PreviewNotFoundError(prev_uuid)

        doc = await cls._get_document(session, preview.document_id)
        await verify_current_document_permission(session, doc, principal_id, "edit")

        if preview.status in (PreviewStatus.APPLIED.value, "committed"):
            raise ApprovalInvalidError("Cannot reject a preview that has already been committed.")

        preview.status = "rejected"
        stmt_app = select(MhDocumentApproval).where(MhDocumentApproval.preview_id == preview.id)
        approval = (await session.execute(stmt_app)).scalar_one_or_none()
        if approval is not None:
            approval.status = ApprovalStatus.REJECTED.value

        await session.commit()
        res = _serialize_preview(preview, doc)
        if reason:
            res["rejection_reason"] = reason
        return res

    @classmethod
    async def commit_preview(
        cls,
        session: AsyncSession,
        store: V2ObjectStore,
        *,
        principal_id: str,
        preview_id: uuid.UUID | str,
        base_revision: int,
        patch_hash: str,
        idempotency_key: str,
        base_sha256: str | None = None,
        artifact_sha256: str | None = None,
        raw_approval_payload: dict[str, Any] | None = None,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Atomically approves and commits a preview in a single database transaction.

        Steps:
        1. Recheck current write permission on the target document (even on idempotent replay).
        2. Check `MhIdempotencyRecord` for `(principal_id, 'commit_preview', idempotency_key)`:
           - Same request fingerprint -> return cached committed revision (`idempotent_replay: True`).
           - Different request fingerprint -> raise HTTP 409 `IDEMPOTENCY_CONFLICT`.
        3. Verify base revision bytes and staged artifact bytes in `V2ObjectStore`, and recompute
           canonical `patch_hash` over verified bytes.
        4. In ONE database transaction:
           - Recheck live write permission, 15m approval expiry, base_revision, and patch_hash.
           - Consume `MhDocumentApproval` (`status='CONSUMED'`, `consumed_at=now`).
           - Transition `MhStagedObject` (`VERIFIED` -> `COMMITTED`).
           - Insert `MhDocumentRevision` (`revision_no = base_revision + 1`, `commit_status='COMMITTED'`).
           - Update `MhDocument.current_revision_id` and `current_revision_no`.
           - Mark `MhEditPreview.status = 'committed'`.
           - Insert `MhOutbox` event (`document.revision.committed`).
           - Insert `MhIdempotencyRecord`.
           - Verify `now < expires_at` immediately before commit completes.
        """
        clean_idem_key = str(idempotency_key or "").strip()
        if not clean_idem_key:
            raise RevisionError(
                "idempotency_key is required for commit_preview.",
                code="INVALID_ARGUMENT",
                status_code=422,
            )

        prev_uuid = _coerce_uuid(preview_id, "preview_id")
        stmt_prev = (
            select(MhEditPreview)
            .where(MhEditPreview.id == prev_uuid)
            .execution_options(populate_existing=True)
        )
        preview = (await session.execute(stmt_prev)).scalar_one_or_none()

        # Check existing idempotency record for this principal + key first (in case key was used on another preview)
        stmt_idem = select(MhIdempotencyRecord).where(
            MhIdempotencyRecord.principal_id == principal_id,
            MhIdempotencyRecord.operation_kind == "commit_preview",
            MhIdempotencyRecord.idempotency_key == clean_idem_key,
        )
        existing_idem = (await session.execute(stmt_idem)).scalar_one_or_none()

        req_fingerprint = compute_request_fingerprint(
            principal_id=principal_id,
            operation="commit_preview",
            request={
                "base_revision": int(base_revision),
                "patch_hash": str(patch_hash or "").lower(),
                "preview_id": str(prev_uuid),
            },
        )

        if existing_idem is not None:
            if existing_idem.request_fingerprint != req_fingerprint:
                raise IdempotencyConflictError(clean_idem_key, "commit_preview")

            # Even on valid idempotent replay, current resource authorization MUST be verified
            cached_doc_id = existing_idem.cached_response_json.get("document_id")
            if cached_doc_id:
                doc_for_replay = await cls._get_document(session, cached_doc_id)
                await verify_current_document_permission(session, doc_for_replay, principal_id, "edit")
            elif preview is not None:
                doc_for_replay = await cls._get_document(session, preview.document_id)
                await verify_current_document_permission(session, doc_for_replay, principal_id, "edit")

            return {
                **existing_idem.cached_response_json,
                "idempotent_replay": True,
            }

        if preview is None:
            raise PreviewNotFoundError(prev_uuid)

        # Lock document row for serialized publication boundary
        stmt_doc = (
            select(MhDocument)
            .where(MhDocument.id == preview.document_id, MhDocument.is_archived.is_(False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        doc = (await session.execute(stmt_doc)).scalar_one_or_none()
        if doc is None:
            raise DocumentNotFoundError(preview.document_id)

        # 1. Recheck current write permission at commit time
        await verify_current_document_permission(session, doc, principal_id, "edit")

        # 2. Fetch approval record
        stmt_app = (
            select(MhDocumentApproval)
            .where(MhDocumentApproval.preview_id == preview.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        approval = (await session.execute(stmt_app)).scalar_one_or_none()

        # 3. Validate approval bindings, LLM self-approval rejection, 15m expiry, patch_hash, and base_revision
        try:
            commit_time = validate_approval_for_commit(
                preview=preview,
                approval_record=approval,
                document=doc,
                principal_id=principal_id,
                supplied_base_revision=int(base_revision),
                supplied_patch_hash=str(patch_hash or ""),
                supplied_base_sha256=base_sha256,
                supplied_artifact_sha256=artifact_sha256,
                raw_approval_payload=raw_approval_payload,
                now_fn=now_fn,
            )
        except ApprovalExpiredError:
            await session.commit()
            raise

        # 4. Pre-commit byte verification: verify base revision and staged object in V2ObjectStore
        base_rev = await cls._get_current_revision(session, doc)
        verify_committed_revision_bytes(store, base_rev)

        staged_obj: MhStagedObject | None = None
        if preview.staged_object_id is not None:
            staged_obj = await session.get(MhStagedObject, preview.staged_object_id)

        staged_bytes = verify_staged_blob_before_commit(
            store,
            staged_obj,
            preview.artifact_sha256,
        )

        # Recompute canonical patch_hash over verified bytes
        ops = preview.operations_json if isinstance(preview.operations_json, list) else []
        recomputed_patch_hash = compute_canonical_patch_hash(
            document_id=str(doc.id),
            base_revision=preview.base_revision_no,
            base_sha256=base_rev.checksum_sha256,
            operations=ops,
            artifact_sha256=hashlib.sha256(staged_bytes).hexdigest(),
        )
        if recomputed_patch_hash.lower() != preview.canonical_patch_hash.lower():
            raise ApprovalInvalidError(
                "Recomputed canonical patch_hash does not match preview patch_hash.",
                details={
                    "expected_patch_hash": preview.canonical_patch_hash,
                    "recomputed_patch_hash": recomputed_patch_hash,
                },
            )

        # 5. Single-transaction atomic publication mutations
        approver_str = str(principal_id)
        if approval is not None:
            if approval.status != ApprovalStatus.PENDING.value:
                raise ApprovalInvalidError(
                    f"Approval record is already in status '{approval.status}'."
                )
            approval.status = ApprovalStatus.CONSUMED.value
            approval.consumed_at = commit_time
            approval.approver_user_id = approver_str
        else:
            approval = MhDocumentApproval(
                id=uuid.uuid4(),
                preview_id=preview.id,
                document_id=doc.id,
                base_revision_no=preview.base_revision_no,
                canonical_patch_hash=preview.canonical_patch_hash,
                approver_user_id=approver_str,
                status=ApprovalStatus.CONSUMED.value,
                consumed_at=commit_time,
                expires_at=compute_fixed_approval_expiry(preview.created_at),
                created_at=preview.created_at,
            )
            session.add(approval)

        if staged_obj is not None:
            validate_stage_transition(staged_obj.stage_status, PublicationState.COMMITTED.value)
            staged_obj.stage_status = PublicationState.COMMITTED.value
            object_key = staged_obj.staged_object_key
        else:
            object_key = f"{store.prefix}blobs/{preview.artifact_sha256}"

        new_rev_no = doc.current_revision_no + 1
        new_rev_id = uuid.uuid4()
        new_rev = MhDocumentRevision(
            id=new_rev_id,
            document_id=doc.id,
            revision_no=new_rev_no,
            object_key=object_key,
            checksum_sha256=preview.artifact_sha256,
            byte_size=len(staged_bytes),
            source_metadata={
                "preview_id": str(preview.id),
                "approval_id": str(approval.id),
                "base_revision_no": preview.base_revision_no,
                "canonical_patch_hash": preview.canonical_patch_hash,
                "operations_count": len(ops),
            },
            commit_status=PublicationState.COMMITTED.value,
            committed_by=principal_id,
            committed_at=commit_time,
        )
        session.add(new_rev)
        await session.flush()

        doc.current_revision_id = new_rev.id
        doc.current_revision_no = new_rev_no
        doc.updated_at = commit_time
        preview.status = PreviewStatus.APPLIED.value

        outbox_event = insert_outbox_event(
            session=session,
            event_key=f"doc:{doc.id}:rev:{new_rev_no}",
            event_type="document.revision.committed",
            aggregate_type="mh_document",
            aggregate_id=str(doc.id),
            payload={
                "document_id": str(doc.id),
                "revision_id": str(new_rev.id),
                "revision_no": new_rev_no,
                "base_revision_no": preview.base_revision_no,
                "preview_id": str(preview.id),
                "approval_id": str(approval.id),
                "canonical_patch_hash": preview.canonical_patch_hash,
                "checksum_sha256": new_rev.checksum_sha256,
                "committed_by": principal_id,
                "committed_at": commit_time.isoformat(),
            },
        )

        response_payload = {
            **_serialize_revision(new_rev),
            "preview_id": str(preview.id),
            "approval_id": str(approval.id),
            "base_revision_no": preview.base_revision_no,
            "patch_hash": preview.canonical_patch_hash,
            "canonical_patch_hash": preview.canonical_patch_hash,
            "outbox_event_id": str(outbox_event.id),
            "outbox_event_key": outbox_event.event_key,
            "idempotent_replay": False,
        }

        idem_record = MhIdempotencyRecord(
            id=uuid.uuid4(),
            principal_id=principal_id,
            operation_kind="commit_preview",
            idempotency_key=clean_idem_key,
            request_fingerprint=req_fingerprint,
            response_status_code=200,
            cached_response_json=response_payload,
            expires_at=commit_time + IDEMPOTENCY_TTL,
            created_at=commit_time,
        )
        session.add(idem_record)

        # Final pre-commit clock check (D04b race invariant)
        final_now = ensure_utc(now_fn())
        fixed_expires_at = compute_fixed_approval_expiry(preview.created_at)
        if final_now >= min(fixed_expires_at, ensure_utc(preview.expires_at)):
            await session.rollback()
            raise ApprovalExpiredError(preview.id, fixed_expires_at)

        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RevisionConflictError(
                doc.id,
                expected_base=preview.base_revision_no,
                current_revision=doc.current_revision_no,
            ) from exc

        return response_payload

    @classmethod
    async def rollback_document(
        cls,
        session: AsyncSession,
        store: V2ObjectStore,
        *,
        principal_id: str,
        document_id: uuid.UUID | str,
        target_revision: int,
        expected_current_revision: int | None = None,
        reason: str = "Compensating rollback to historical revision",
        idempotency_key: str | None = None,
        now_fn: Any = utcnow,
    ) -> dict[str, Any]:
        """Creates a new compensating revision (`N + 1`) pointing to historical target's verified bytes.

        Never deletes or overwrites prior revision rows, preserving the complete audit history.
        """
        doc_uuid = _coerce_uuid(document_id, "document_id")
        stmt_doc = (
            select(MhDocument)
            .where(MhDocument.id == doc_uuid, MhDocument.is_archived.is_(False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        doc = (await session.execute(stmt_doc)).scalar_one_or_none()
        if doc is None:
            raise DocumentNotFoundError(doc_uuid)

        # 1. Verify current write permission
        await verify_current_document_permission(session, doc, principal_id, "edit")

        clean_idem_key = str(idempotency_key).strip() if idempotency_key else None
        req_fingerprint: str | None = None
        if clean_idem_key:
            req_fingerprint = compute_request_fingerprint(
                principal_id=principal_id,
                operation="rollback_document",
                request={
                    "document_id": str(doc.id),
                    "expected_current_revision": expected_current_revision,
                    "reason": str(reason or ""),
                    "target_revision": int(target_revision),
                },
            )
            stmt_idem = select(MhIdempotencyRecord).where(
                MhIdempotencyRecord.principal_id == principal_id,
                MhIdempotencyRecord.operation_kind == "rollback_document",
                MhIdempotencyRecord.idempotency_key == clean_idem_key,
            )
            existing_idem = (await session.execute(stmt_idem)).scalar_one_or_none()
            if existing_idem is not None:
                if existing_idem.request_fingerprint != req_fingerprint:
                    raise IdempotencyConflictError(clean_idem_key, "rollback_document")
                return {
                    **existing_idem.cached_response_json,
                    "idempotent_replay": True,
                }

        if expected_current_revision is not None and int(expected_current_revision) != doc.current_revision_no:
            raise RevisionConflictError(
                doc.id,
                expected_base=int(expected_current_revision),
                current_revision=doc.current_revision_no,
            )

        target_rev_no = int(target_revision)
        if target_rev_no < 1 or target_rev_no >= doc.current_revision_no:
            raise RevisionError(
                f"Invalid rollback target_revision {target_rev_no}: must be between 1 and {doc.current_revision_no - 1}.",
                code="REVISION_CONFLICT" if target_rev_no == doc.current_revision_no else "INVALID_ARGUMENT",
                status_code=409 if target_rev_no == doc.current_revision_no else 422,
                details={
                    "target_revision": target_rev_no,
                    "current_revision": doc.current_revision_no,
                },
            )

        # 2. Fetch target historical revision and current faulty revision
        stmt_target = select(MhDocumentRevision).where(
            MhDocumentRevision.document_id == doc.id,
            MhDocumentRevision.revision_no == target_rev_no,
        )
        target_rev = (await session.execute(stmt_target)).scalar_one_or_none()
        if target_rev is None:
            raise RevisionError(
                f"Target historical revision {target_rev_no} not found for document '{doc.id}'.",
                code="NOT_FOUND",
                status_code=404,
            )

        faulty_rev = await cls._get_current_revision(session, doc)

        # 3. Fail-closed verification of target historical bytes in V2ObjectStore
        verified_bytes = verify_committed_revision_bytes(store, target_rev)

        now = ensure_utc(now_fn())
        new_rev_no = doc.current_revision_no + 1
        new_rev_id = uuid.uuid4()

        # 4. Update MhObjectRegistry reference count for shared checksum blob
        obj_reg = await session.get(MhObjectRegistry, target_rev.checksum_sha256)
        if obj_reg is not None:
            obj_reg.ref_count += 1
            obj_reg.gc_status = "ACTIVE"
            obj_reg.last_verified_at = now

        # 5. Insert new forward compensating MhDocumentRevision
        comp_rev = MhDocumentRevision(
            id=new_rev_id,
            document_id=doc.id,
            revision_no=new_rev_no,
            object_key=target_rev.object_key,
            checksum_sha256=target_rev.checksum_sha256,
            byte_size=len(verified_bytes),
            source_metadata={
                "compensating_rollback": True,
                "rollback_from_revision": faulty_rev.revision_no,
                "rollback_to_revision": target_rev.revision_no,
                "rollback_reason": reason,
            },
            commit_status=PublicationState.COMMITTED.value,
            committed_by=principal_id,
            committed_at=now,
        )
        session.add(comp_rev)
        await session.flush()

        # 6. Insert MhCompensatingRevision lineage record
        comp_lineage = MhCompensatingRevision(
            id=uuid.uuid4(),
            document_id=doc.id,
            faulty_revision_id=faulty_rev.id,
            compensating_revision_id=comp_rev.id,
            faulty_revision_no=faulty_rev.revision_no,
            compensating_revision_no=comp_rev.revision_no,
            rollback_reason=str(reason or "Compensating rollback"),
            initiated_by=str(principal_id),
            created_at=now,
        )
        session.add(comp_lineage)

        # 7. Update MhDocument current pointer
        doc.current_revision_id = comp_rev.id
        doc.current_revision_no = new_rev_no
        doc.updated_at = now

        # 8. Insert outbox event in same transaction
        outbox_event = insert_outbox_event(
            session=session,
            event_key=f"doc:{doc.id}:rev:{new_rev_no}",
            event_type="document.revision.rolled_back",
            aggregate_type="mh_document",
            aggregate_id=str(doc.id),
            payload={
                "document_id": str(doc.id),
                "revision_id": str(comp_rev.id),
                "revision_no": new_rev_no,
                "faulty_revision_no": faulty_rev.revision_no,
                "restored_from_revision_no": target_rev.revision_no,
                "checksum_sha256": comp_rev.checksum_sha256,
                "reason": reason,
                "initiated_by": principal_id,
                "committed_at": now.isoformat(),
            },
        )

        response_payload = {
            **_serialize_revision(comp_rev),
            "compensating_record_id": str(comp_lineage.id),
            "faulty_revision_no": faulty_rev.revision_no,
            "restored_from_revision_no": target_rev.revision_no,
            "target_revision": target_rev.revision_no,
            "rollback_reason": reason,
            "outbox_event_id": str(outbox_event.id),
            "outbox_event_key": outbox_event.event_key,
            "idempotent_replay": False,
        }

        if clean_idem_key and req_fingerprint:
            idem_record = MhIdempotencyRecord(
                id=uuid.uuid4(),
                principal_id=principal_id,
                operation_kind="rollback_document",
                idempotency_key=clean_idem_key,
                request_fingerprint=req_fingerprint,
                response_status_code=200,
                cached_response_json=response_payload,
                expires_at=now + IDEMPOTENCY_TTL,
                created_at=now,
            )
            session.add(idem_record)

        await session.commit()
        return response_payload

    @classmethod
    async def list_revisions(
        cls,
        session: AsyncSession,
        *,
        principal_id: str,
        document_id: uuid.UUID | str,
    ) -> list[dict[str, Any]]:
        """Lists committed/available revisions for a document after verifying read permission."""
        doc = await cls._get_document(session, document_id)
        await verify_current_document_permission(session, doc, principal_id, "read")
        revisions = await get_committed_revisions(session, doc.id)
        return [_serialize_revision(r) for r in revisions]

    @classmethod
    async def read_committed_content(
        cls,
        session: AsyncSession,
        store: V2ObjectStore,
        *,
        principal_id: str,
        document_id: uuid.UUID | str,
        revision_no: int | None = None,
    ) -> tuple[bytes, MhDocument, MhDocumentRevision]:
        """Reads and verifies committed document bytes (failing closed on corruption/missing blob)."""
        doc = await cls._get_document(session, document_id)
        await verify_current_document_permission(session, doc, principal_id, "read")

        if revision_no is None:
            rev = await cls._get_current_revision(session, doc)
        else:
            stmt = select(MhDocumentRevision).where(
                MhDocumentRevision.document_id == doc.id,
                MhDocumentRevision.revision_no == int(revision_no),
            )
            rev = (await session.execute(stmt)).scalar_one_or_none()
            if rev is None:
                raise RevisionError(
                    f"Revision {revision_no} not found for document '{doc.id}'.",
                    code="NOT_FOUND",
                    status_code=404,
                )

        content = verify_committed_revision_bytes(store, rev)
        return content, doc, rev
