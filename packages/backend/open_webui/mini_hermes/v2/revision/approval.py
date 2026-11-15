"""15-Minute Approval Binding, Expiry Protocol & Resource Permission Recheck (M4.3).

Enforces:
1. Approval binds `user_id`, `document_id`, `base_revision`, canonical `patch_hash`,
   `approver_id`, and `expires_at = preview.created_at + 15 minutes`.
2. Retries NEVER extend expiration (`expires_at` is strictly anchored to `preview.created_at + 15 minutes`).
3. Never trusts approval state supplied solely by the LLM or AgentRuntime.
4. Rechecks current resource permission via Auth Service / V2 RBAC (`mh_users`, `mh_access_grants`,
   `mh_group_memberships`, `mh_folder_nodes`) at preview creation, preview read, commit, and replay.
   - HTTP 403 `FORBIDDEN` on missing or revoked permission, or deactivated/pending principal.
   - HTTP 409 `APPROVAL_INVALID_OR_EXPIRED` on expired approval or patch hash mismatch.
   - HTTP 409 `REVISION_CONFLICT` on concurrent base revision advance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.mini_hermes.v2.models import (
    ApprovalStatus,
    MhAccessGrant,
    MhDocument,
    MhDocumentApproval,
    MhEditPreview,
    MhFolderNode,
    MhGroupMembership,
    MhUser,
    PreviewStatus,
    utcnow,
)
from open_webui.mini_hermes.v2.revision.state_machine import RevisionError

APPROVAL_TTL_SECONDS = 900  # 15 minutes
APPROVAL_TTL = timedelta(minutes=15)

READ_PERMISSIONS = {"read", "edit", "write", "admin"}
WRITE_PERMISSIONS = {"edit", "write", "admin"}

# Keys that indicate an LLM or AgentRuntime attempted to self-approve a document mutation
FORBIDDEN_LLM_SELF_APPROVAL_KEYS = {
    "llm_approved",
    "agent_approved",
    "agent_runtime_approved",
    "auto_approve",
    "bypass_approval",
}


class PermissionDeniedError(RevisionError):
    """Raised when the current principal lacks required document permission (HTTP 403)."""

    def __init__(self, document_id: uuid.UUID | str, principal_id: str, required: str) -> None:
        super().__init__(
            f"Principal '{principal_id}' lacks '{required}' permission on document '{document_id}'.",
            code="FORBIDDEN",
            status_code=403,
            retryable=False,
            details={"document_id": str(document_id), "required_permission": required},
        )


class ApprovalExpiredError(RevisionError):
    """Raised when a preview or approval has exceeded its strict 15-minute window (HTTP 409)."""

    def __init__(self, preview_id: uuid.UUID | str, expires_at: datetime) -> None:
        super().__init__(
            f"Approval window (15 minutes) for preview '{preview_id}' expired at {expires_at.isoformat()}. "
            "Create a fresh preview to review and commit.",
            code="APPROVAL_INVALID_OR_EXPIRED",
            status_code=409,
            retryable=False,
            details={
                "legacy_code": "approval_expired",
                "preview_id": str(preview_id),
                "expires_at": expires_at.isoformat(),
            },
        )


class ApprovalInvalidError(RevisionError):
    """Raised when approval parameters or patch hash do not match the preview (HTTP 409)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="APPROVAL_INVALID_OR_EXPIRED",
            status_code=409,
            retryable=False,
            details={"legacy_code": "patch_hash_mismatch", **(details or {})},
        )


class RevisionConflictError(RevisionError):
    """Raised when the document's current revision has advanced past base_revision (HTTP 409)."""

    def __init__(self, document_id: uuid.UUID | str, expected_base: int, current_revision: int) -> None:
        super().__init__(
            f"Revision conflict on document '{document_id}': preview base_revision is {expected_base}, "
            f"but current committed revision is {current_revision}.",
            code="REVISION_CONFLICT",
            status_code=409,
            retryable=False,
            details={
                "document_id": str(document_id),
                "base_revision": expected_base,
                "current_revision": current_revision,
            },
        )


def ensure_utc(dt_val: datetime) -> datetime:
    """Ensures a datetime is timezone-aware in UTC (handling SQLite naive timestamps)."""
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def compute_fixed_approval_expiry(preview_created_at: datetime) -> datetime:
    """Computes the immutable 15-minute expiration timestamp anchored to preview creation."""
    return ensure_utc(preview_created_at) + APPROVAL_TTL


async def verify_current_document_permission(
    session: AsyncSession,
    document: MhDocument,
    principal_id: str,
    required_permission: str = "edit",
) -> None:
    """Verifies current resource authorization for `principal_id` on `document`.

    Checks:
    1. Principal identity must be non-empty and not an unauthenticated agent/LLM token.
    2. If a user record exists in `mh_users`, verifies `is_active == True` and `role != 'pending'`.
       If deactivated or pending, denies immediately with HTTP 403 `FORBIDDEN`.
    3. If user has live `role == 'admin'` in `mh_users`, access is granted.
    4. If `document.owner_id == principal_id` (and owner is not deactivated), access is granted.
    5. Otherwise, checks `mh_access_grants` on the document and its ancestor `mh_folder_nodes`
       for direct user grants or group grants via `mh_group_memberships`.
    6. Denies with HTTP 403 `FORBIDDEN` if current authorization cannot be verified.
    """
    if not principal_id or not str(principal_id).strip():
        raise PermissionDeniedError(document.id, str(principal_id), required_permission)

    clean_principal = str(principal_id).strip()
    if clean_principal.lower() in {"llm", "agent", "agent_runtime", "anonymous", "unauthenticated"}:
        raise PermissionDeniedError(document.id, clean_principal, required_permission)

    allowed_perms = WRITE_PERMISSIONS if required_permission in ("edit", "write", "admin") else READ_PERMISSIONS

    # Check live user status in mh_users if principal is a UUID
    user_uuid: uuid.UUID | None = None
    try:
        user_uuid = uuid.UUID(clean_principal)
    except ValueError:
        user_uuid = None

    user_row: MhUser | None = None
    if user_uuid is not None:
        stmt_u = (
            select(MhUser)
            .where(MhUser.id == user_uuid)
            .execution_options(populate_existing=True)
        )
        user_row = (await session.execute(stmt_u)).scalar_one_or_none()

    if user_row is not None:
        if not user_row.is_active or user_row.role in ("pending", "disabled", "revoked"):
            raise PermissionDeniedError(document.id, clean_principal, required_permission)
        if user_row.role == "admin":
            return

    # Owner check (only after confirming user_row, if present, is active)
    if str(document.owner_id) == clean_principal:
        return

    # Check discretionary access grants (resource_id and grantee_id are Uuid(as_uuid=True))
    grantee_uuids: list[uuid.UUID] = []
    if user_uuid is not None:
        grantee_uuids.append(user_uuid)
    else:
        grantee_uuids.append(uuid.uuid5(uuid.NAMESPACE_DNS, f"mh.user.{clean_principal}"))

    # Gather group memberships for user
    stmt_groups = select(MhGroupMembership.group_id).where(
        MhGroupMembership.user_id.in_(grantee_uuids)
    )
    group_ids = list((await session.execute(stmt_groups)).scalars().all())

    # 1. Check direct document grants
    stmt_doc_grants = select(MhAccessGrant).where(
        MhAccessGrant.resource_type.in_(["document", "mh_document"]),
        MhAccessGrant.resource_id == document.id,
        MhAccessGrant.permission.in_(allowed_perms),
    )
    doc_grants = list((await session.execute(stmt_doc_grants)).scalars().all())
    for grant in doc_grants:
        if grant.grantee_type == "user" and grant.grantee_id in grantee_uuids:
            return
        if grant.grantee_type == "group" and grant.grantee_id in group_ids:
            return

    # 2. Walk folder hierarchy for inherited folder grants or folder ownership
    curr_folder_id = document.folder_id
    visited_folders: set[uuid.UUID] = set()
    while curr_folder_id is not None and curr_folder_id not in visited_folders:
        visited_folders.add(curr_folder_id)
        folder = await session.get(MhFolderNode, curr_folder_id)
        if folder is None:
            break
        if str(folder.owner_id) == clean_principal or folder.owner_id in grantee_uuids:
            return

        stmt_folder_grants = select(MhAccessGrant).where(
            MhAccessGrant.resource_type.in_(["folder", "mh_folder"]),
            MhAccessGrant.resource_id == folder.id,
            MhAccessGrant.permission.in_(allowed_perms),
        )
        folder_grants = list((await session.execute(stmt_folder_grants)).scalars().all())
        for grant in folder_grants:
            if grant.grantee_type == "user" and grant.grantee_id in grantee_uuids:
                return
            if grant.grantee_type == "group" and grant.grantee_id in group_ids:
                return

        curr_folder_id = folder.parent_folder_id

    raise PermissionDeniedError(document.id, clean_principal, required_permission)


def validate_approval_for_commit(
    *,
    preview: MhEditPreview,
    approval_record: MhDocumentApproval | None,
    document: MhDocument,
    principal_id: str,
    supplied_base_revision: int,
    supplied_patch_hash: str,
    supplied_base_sha256: str | None = None,
    supplied_artifact_sha256: str | None = None,
    raw_approval_payload: dict[str, Any] | None = None,
    now_fn: Any = utcnow,
) -> datetime:
    """Validates all approval bindings, 15-minute expiry, base revision, and canonical patch hash.

    Returns the `now` UTC timestamp at which the approval was validated.
    """
    now = ensure_utc(now_fn())

    # 1. Reject LLM / AgentRuntime self-approval flags
    if raw_approval_payload:
        for forbidden_key in FORBIDDEN_LLM_SELF_APPROVAL_KEYS:
            if raw_approval_payload.get(forbidden_key):
                raise ApprovalInvalidError(
                    f"Approval cannot be supplied or bypassed by LLM or AgentRuntime ('{forbidden_key}'). "
                    "Only an authenticated human approver via Revision Service may approve a commit."
                )
        approver_source = str(raw_approval_payload.get("approval_source") or "").lower()
        if approver_source in {"llm", "agent", "agent_runtime"}:
            raise ApprovalInvalidError(
                f"Invalid approval_source '{approver_source}': never trust approval state from LLM or AgentRuntime."
            )

    # 2. Enforce strict 15-minute approval window anchored to preview.created_at
    fixed_expires_at = compute_fixed_approval_expiry(preview.created_at)
    preview_expires_at = ensure_utc(preview.expires_at)
    effective_expires_at = min(fixed_expires_at, preview_expires_at)

    if approval_record is not None:
        record_expires_at = ensure_utc(approval_record.expires_at)
        effective_expires_at = min(effective_expires_at, record_expires_at)

    if now >= effective_expires_at or preview.status == PreviewStatus.EXPIRED.value:
        preview.status = PreviewStatus.EXPIRED.value
        if approval_record is not None and approval_record.status == ApprovalStatus.PENDING.value:
            approval_record.status = ApprovalStatus.EXPIRED.value
        raise ApprovalExpiredError(preview.id, effective_expires_at)

    # 3. Verify preview status is ready
    if preview.status != PreviewStatus.READY.value:
        raise ApprovalInvalidError(
            f"Preview '{preview.id}' is in status '{preview.status}' and cannot be committed.",
            details={"preview_status": preview.status},
        )

    # 4. Verify canonical patch_hash binding
    if not supplied_patch_hash or supplied_patch_hash.lower() != preview.canonical_patch_hash.lower():
        raise ApprovalInvalidError(
            "Canonical patch_hash mismatch: the approved patch hash does not match the preview's exact patch.",
            details={
                "expected_patch_hash": preview.canonical_patch_hash,
                "supplied_patch_hash": supplied_patch_hash,
            },
        )

    if approval_record is not None and approval_record.canonical_patch_hash.lower() != preview.canonical_patch_hash.lower():
        raise ApprovalInvalidError(
            "Stored approval record patch_hash does not match preview canonical_patch_hash."
        )

    # 5. Verify optional base_sha256 and artifact_sha256 bindings if provided by caller
    if supplied_base_sha256 is not None and supplied_base_sha256.lower() != preview.base_sha256.lower():
        raise ApprovalInvalidError(
            "base_sha256 mismatch against preview base revision.",
            details={"expected_base_sha256": preview.base_sha256, "supplied_base_sha256": supplied_base_sha256},
        )

    if supplied_artifact_sha256 is not None and supplied_artifact_sha256.lower() != preview.artifact_sha256.lower():
        raise ApprovalInvalidError(
            "artifact_sha256 mismatch against preview staged artifact.",
            details={
                "expected_artifact_sha256": preview.artifact_sha256,
                "supplied_artifact_sha256": supplied_artifact_sha256,
            },
        )

    # 6. Verify base_revision matches both preview and current document revision
    if int(supplied_base_revision) != preview.base_revision_no:
        raise RevisionConflictError(
            document.id,
            expected_base=preview.base_revision_no,
            current_revision=int(supplied_base_revision),
        )

    if document.current_revision_no != preview.base_revision_no:
        raise RevisionConflictError(
            document.id,
            expected_base=preview.base_revision_no,
            current_revision=document.current_revision_no,
        )

    return now
