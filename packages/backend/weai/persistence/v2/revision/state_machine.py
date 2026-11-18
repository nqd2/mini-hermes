"""Revision Service Publication State Machine (M4.1).

Enforces the strict lifecycle on ar_document_revisions and ar_staged_objects:
    STAGED -> VERIFIED -> COMMITTED -> AVAILABLE

Guarantees:
1. Immutable checksum-addressed staged blobs (`v2/blobs/{sha256}`) verified by readback before publication.
2. Staged objects never appear as committed revisions.
3. Abandoned staged object recovery after a configurable safety interval, respecting shared checksum references across documents and revisions.
4. Fail-closed verification with critical operator alert if a committed object's checksum or presence is invalid.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from weai.persistence.v2.models import (
    WeAIDocument,
    WeAIDocumentRevision,
    WeAIObjectRegistry,
    WeAIStagedObject,
    PublicationState,
    utcnow,
)
from weai.persistence.v2.storage import (
    StorageChecksumMismatchError,
    StorageNamespaceViolationError,
    V2ObjectStore,
)

log = logging.getLogger(__name__)

# Valid forward-only publication state transitions
VALID_PUBLICATION_TRANSITIONS: dict[str, set[str]] = {
    PublicationState.STAGED.value: {PublicationState.VERIFIED.value},
    PublicationState.VERIFIED.value: {PublicationState.COMMITTED.value},
    PublicationState.COMMITTED.value: {
        PublicationState.COMMITTED.value,
        PublicationState.AVAILABLE.value,
    },
    PublicationState.AVAILABLE.value: {PublicationState.AVAILABLE.value},
}

VALID_STAGE_TRANSITIONS: dict[str, set[str]] = {
    "STAGED": {"VERIFIED", "EXPIRED", "DELETED"},
    "VERIFIED": {"COMMITTED", "EXPIRED", "DELETED"},
    "COMMITTED": {"COMMITTED"},
    "EXPIRED": {"DELETED"},
    "DELETED": {"DELETED"},
}

DEFAULT_STAGE_TTL_SECONDS = 900  # 15 minutes matching approval TTL
DEFAULT_SAFETY_INTERVAL_SECONDS = 3600  # 1 hour safety interval for abandoned stages


class RevisionError(Exception):
    """Base exception for V2 Revision Service operations."""

    def __init__(
        self,
        message: str,
        code: str = "REVISION_ERROR",
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **self.details,
        }


class InvalidStateTransitionError(RevisionError):
    """Raised when an invalid or backward publication state transition is attempted."""

    def __init__(self, entity: str, current_state: str, target_state: str) -> None:
        super().__init__(
            f"Invalid {entity} state transition: '{current_state}' -> '{target_state}'. "
            "Lifecycle must follow STAGED -> VERIFIED -> COMMITTED -> AVAILABLE.",
            code="INVALID_STATE_TRANSITION",
            status_code=409,
            retryable=False,
            details={"current_state": current_state, "target_state": target_state},
        )


class StageVerificationError(RevisionError):
    """Raised when staged object verification fails before publication."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="STAGE_VERIFICATION_FAILED",
            status_code=409,
            retryable=False,
            details=details,
        )


class RevisionIntegrityFailureError(RevisionError):
    """Raised when a committed revision's stored object is missing or checksum-corrupted."""

    def __init__(
        self,
        document_id: uuid.UUID | str,
        revision_id: uuid.UUID | str,
        revision_no: int,
        reason: str,
    ) -> None:
        super().__init__(
            f"Committed document revision {revision_no} failed integrity verification: {reason}",
            code="INTEGRITY_FAILURE",
            status_code=503,
            retryable=False,
            details={
                "legacy_code": "document_revision_integrity_failure",
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "revision": revision_no,
                "reason": reason,
            },
        )


def validate_publication_transition(current_state: str, target_state: str) -> None:
    """Enforces forward-only STAGED -> VERIFIED -> COMMITTED -> AVAILABLE transitions."""
    allowed = VALID_PUBLICATION_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise InvalidStateTransitionError("revision", current_state, target_state)


def validate_stage_transition(current_state: str, target_state: str) -> None:
    """Enforces valid stage status transitions on WeAIStagedObject."""
    allowed = VALID_STAGE_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise InvalidStateTransitionError("staged_object", current_state, target_state)


async def stage_and_verify_artifact(
    session: AsyncSession,
    store: V2ObjectStore,
    data: bytes,
    *,
    document_id: uuid.UUID,
    principal_id: str,
    ttl_seconds: int = DEFAULT_STAGE_TTL_SECONDS,
    now_fn: Any = utcnow,
) -> tuple[WeAIStagedObject, str, str]:
    """Stages immutable bytes under `v2/blobs/{sha256}` and transitions STAGED -> VERIFIED.

    Steps:
    1. Compute SHA-256 digest of `data`.
    2. Register or update `WeAIObjectRegistry` row (refusing attachment to DELETE_CLAIMED generation).
    3. Create `WeAIStagedObject` in `STAGED` state.
    4. Write immutable bytes to `V2ObjectStore` under `v2/blobs/{sha256}`.
    5. Read back stored bytes and verify exact byte size and SHA-256 checksum.
    6. Transition `WeAIStagedObject` from `STAGED` -> `VERIFIED` and set `verified_at`.

    Returns `(staged_object, scoped_key, sha256_hex)`.
    """
    now = now_fn()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    byte_size = len(data)
    canonical_key = f"{store.prefix}blobs/{actual_sha256}"

    # 1. Synchronize with WeAIObjectRegistry
    obj_reg = await session.get(WeAIObjectRegistry, actual_sha256)
    if obj_reg is not None:
        if obj_reg.gc_status == "DELETE_CLAIMED":
            raise StageVerificationError(
                f"Object key '{canonical_key}' is currently claimed for garbage collection deletion; "
                "cannot attach new stage to a delete-claimed generation.",
                details={"checksum_sha256": actual_sha256, "gc_generation": obj_reg.gc_generation},
            )
        if obj_reg.byte_size != byte_size:
            raise StageVerificationError(
                f"Registry byte size mismatch for SHA-256 {actual_sha256}: "
                f"registered {obj_reg.byte_size}, got {byte_size}."
            )
        if obj_reg.gc_status == "DELETED":
            obj_reg.gc_status = "ACTIVE"
            obj_reg.gc_generation += 1
            obj_reg.ref_count = 1
        else:
            obj_reg.ref_count += 1
        obj_reg.last_verified_at = now
    else:
        obj_reg = WeAIObjectRegistry(
            checksum_sha256=actual_sha256,
            canonical_object_key=canonical_key,
            byte_size=byte_size,
            storage_tier="HOT",
            ref_count=1,
            gc_status="ACTIVE",
            gc_generation=1,
            last_verified_at=now,
            created_at=now,
        )
        session.add(obj_reg)

    await session.flush()

    # 2. Insert WeAIStagedObject in initial STAGED state
    staged_obj = WeAIStagedObject(
        id=uuid.uuid4(),
        checksum_sha256=actual_sha256,
        staged_object_key=canonical_key,
        document_id=document_id,
        principal_id=principal_id,
        byte_size=byte_size,
        stage_status=PublicationState.STAGED.value,
        verified_at=None,
        expires_at=now + timedelta(seconds=ttl_seconds),
        created_at=now,
    )
    session.add(staged_obj)
    await session.flush()

    # 3. Write immutable bytes to V2ObjectStore and verify via readback
    try:
        scoped_key, written_sha256 = store.put_immutable(data, expected_sha256=actual_sha256)
        readback = store.get_bytes(scoped_key)
    except (StorageChecksumMismatchError, StorageNamespaceViolationError, FileNotFoundError, OSError) as exc:
        raise StageVerificationError(f"Failed to write or read back staged blob: {exc}") from exc

    readback_sha256 = hashlib.sha256(readback).hexdigest()
    if readback_sha256 != actual_sha256 or len(readback) != byte_size:
        log.critical(
            "Staged artifact readback integrity failure: stage_id=%s expected_sha256=%s actual_sha256=%s",
            staged_obj.id,
            actual_sha256,
            readback_sha256,
        )
        raise StageVerificationError(
            "Staged artifact readback checksum or size mismatch.",
            details={
                "expected_sha256": actual_sha256,
                "actual_sha256": readback_sha256,
                "expected_size": byte_size,
                "actual_size": len(readback),
            },
        )

    # 4. Transition STAGED -> VERIFIED
    validate_stage_transition(staged_obj.stage_status, PublicationState.VERIFIED.value)
    staged_obj.stage_status = PublicationState.VERIFIED.value
    staged_obj.staged_object_key = scoped_key
    staged_obj.verified_at = now
    await session.flush()

    return staged_obj, scoped_key, actual_sha256


def verify_staged_blob_before_commit(
    store: V2ObjectStore,
    staged_obj: WeAIStagedObject | None,
    expected_sha256: str,
    expected_key: str | None = None,
) -> bytes:
    """Re-verifies stored bytes of a staged artifact immediately prior to publication commit.

    Does not trust DB metadata alone: reads physical bytes from `V2ObjectStore` and
    verifies SHA-256 and byte length.
    """
    if staged_obj is not None:
        if staged_obj.stage_status not in (
            PublicationState.VERIFIED.value,
            PublicationState.COMMITTED.value,
        ):
            raise StageVerificationError(
                f"Staged object {staged_obj.id} is in state '{staged_obj.stage_status}', expected VERIFIED."
            )
        target_key = staged_obj.staged_object_key
    elif expected_key:
        target_key = expected_key
    else:
        target_key = f"blobs/{expected_sha256}"

    try:
        raw_bytes = store.get_bytes(target_key)
    except Exception as exc:
        raise StageVerificationError(
            f"Staged artifact object '{target_key}' is missing or unreadable in object storage: {exc}"
        ) from exc

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise StageVerificationError(
            f"Staged artifact checksum mismatch: expected {expected_sha256}, got {actual_sha256}.",
            details={"expected_sha256": expected_sha256, "actual_sha256": actual_sha256},
        )

    if staged_obj is not None and len(raw_bytes) != staged_obj.byte_size:
        raise StageVerificationError(
            f"Staged artifact byte size mismatch: expected {staged_obj.byte_size}, got {len(raw_bytes)}."
        )

    return raw_bytes


def verify_committed_revision_bytes(
    store: V2ObjectStore,
    revision: WeAIDocumentRevision,
) -> bytes:
    """Reads and verifies a committed revision's bytes from `V2ObjectStore`.

    Fails closed (HTTP 503 `INTEGRITY_FAILURE`) and emits a critical log alert if
    the object is absent, unreadable, or its SHA-256 / byte length does not match
    the immutable revision record.
    """
    if revision.commit_status not in (
        PublicationState.COMMITTED.value,
        PublicationState.AVAILABLE.value,
    ):
        log.critical(
            "Attempted to read uncommitted revision as committed: revision_id=%s document_id=%s status=%s",
            revision.id,
            revision.document_id,
            revision.commit_status,
        )
        raise RevisionIntegrityFailureError(
            document_id=revision.document_id,
            revision_id=revision.id,
            revision_no=revision.revision_no,
            reason=f"Revision status '{revision.commit_status}' is not COMMITTED or AVAILABLE.",
        )

    try:
        content = store.get_bytes(revision.object_key)
    except Exception as exc:
        log.critical(
            "ALERT: Committed document revision object is missing/unavailable! "
            "revision_id=%s document_id=%s revision_no=%s object_key=%s",
            revision.id,
            revision.document_id,
            revision.revision_no,
            revision.object_key,
            exc_info=exc,
        )
        raise RevisionIntegrityFailureError(
            document_id=revision.document_id,
            revision_id=revision.id,
            revision_no=revision.revision_no,
            reason=f"Object '{revision.object_key}' is missing from V2ObjectStore.",
        ) from exc

    actual_sha256 = hashlib.sha256(content).hexdigest()
    actual_size = len(content)
    if (
        actual_sha256.lower() != revision.checksum_sha256.lower()
        or actual_size != revision.byte_size
    ):
        log.critical(
            "ALERT: Committed document revision integrity failure! "
            "revision_id=%s document_id=%s revision_no=%s expected_sha256=%s actual_sha256=%s "
            "expected_size=%s actual_size=%s",
            revision.id,
            revision.document_id,
            revision.revision_no,
            revision.checksum_sha256,
            actual_sha256,
            revision.byte_size,
            actual_size,
        )
        raise RevisionIntegrityFailureError(
            document_id=revision.document_id,
            revision_id=revision.id,
            revision_no=revision.revision_no,
            reason=(
                f"Checksum or size mismatch (expected sha256={revision.checksum_sha256} "
                f"size={revision.byte_size}, got sha256={actual_sha256} size={actual_size})."
            ),
        )

    return content


async def transition_revision_state(
    session: AsyncSession,
    revision: WeAIDocumentRevision,
    target_state: PublicationState | str,
    *,
    store: V2ObjectStore | None = None,
) -> WeAIDocumentRevision:
    """Transitions a revision's `commit_status` along STAGED -> VERIFIED -> COMMITTED -> AVAILABLE."""
    target_val = target_state.value if isinstance(target_state, PublicationState) else str(target_state)
    validate_publication_transition(revision.commit_status, target_val)

    if target_val in (PublicationState.VERIFIED.value, PublicationState.AVAILABLE.value) and store is not None:
        # Verify physical bytes exist and match checksum before marking VERIFIED or AVAILABLE
        try:
            raw = store.get_bytes(revision.object_key)
        except Exception as exc:
            raise RevisionIntegrityFailureError(
                document_id=revision.document_id,
                revision_id=revision.id,
                revision_no=revision.revision_no,
                reason=f"Cannot transition to {target_val}: object '{revision.object_key}' missing.",
            ) from exc
        if hashlib.sha256(raw).hexdigest().lower() != revision.checksum_sha256.lower():
            raise RevisionIntegrityFailureError(
                document_id=revision.document_id,
                revision_id=revision.id,
                revision_no=revision.revision_no,
                reason=f"Cannot transition to {target_val}: checksum mismatch.",
            )

    revision.commit_status = target_val
    await session.flush()
    return revision


async def get_committed_revisions(
    session: AsyncSession,
    document_id: uuid.UUID,
) -> list[WeAIDocumentRevision]:
    """Returns only committed/available revisions for a document, never exposing staged objects."""
    stmt = (
        select(WeAIDocumentRevision)
        .where(
            WeAIDocumentRevision.document_id == document_id,
            WeAIDocumentRevision.commit_status.in_(
                [PublicationState.COMMITTED.value, PublicationState.AVAILABLE.value]
            ),
        )
        .order_by(WeAIDocumentRevision.revision_no.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def cleanup_abandoned_staged_objects(
    session: AsyncSession,
    store: V2ObjectStore,
    *,
    safety_interval_seconds: int = DEFAULT_SAFETY_INTERVAL_SECONDS,
    now_fn: Any = utcnow,
) -> dict[str, Any]:
    """Recovers abandoned staged objects after the safety interval while protecting shared blobs.

    Protocol (per D04a Shared Checksum-Addressed Objects & GC):
    1. Select `WeAIStagedObject` rows in `STAGED`, `VERIFIED`, or `EXPIRED` status whose
       `expires_at <= now - safety_interval_seconds` (or `expires_at <= now` when safety_interval_seconds == 0).
    2. For each candidate stage, mark its `stage_status = 'DELETED'`.
    3. Inspect `WeAIDocumentRevision` and remaining live `WeAIStagedObject` rows for the same `checksum_sha256`.
    4. Only if ZERO committed revisions and ZERO active/unexpired staged objects reference that
       `checksum_sha256` may the physical blob in `V2ObjectStore` be deleted and `WeAIObjectRegistry.gc_status`
       transitioned through `DELETE_CLAIMED` -> `DELETED`.
    5. If any committed revision or live stage shares the same SHA-256, the physical blob is strictly preserved.
    """
    now = now_fn()
    cutoff = now - timedelta(seconds=max(0, safety_interval_seconds))

    stmt = select(WeAIStagedObject).where(
        WeAIStagedObject.stage_status.in_(["STAGED", "VERIFIED", "EXPIRED"]),
        WeAIStagedObject.expires_at <= cutoff,
    )
    res = await session.execute(stmt)
    abandoned_stages = list(res.scalars().all())

    stages_cleaned = 0
    blobs_deleted = 0
    blobs_preserved = 0
    affected_checksums: set[str] = set()

    for stage in abandoned_stages:
        stage.stage_status = "DELETED"
        stages_cleaned += 1
        if stage.checksum_sha256:
            affected_checksums.add(stage.checksum_sha256)

    await session.flush()

    for sha256 in affected_checksums:
        # Count committed/available revisions referencing this checksum
        rev_count_stmt = select(func.count(WeAIDocumentRevision.id)).where(
            WeAIDocumentRevision.checksum_sha256 == sha256,
            WeAIDocumentRevision.commit_status.in_(
                [PublicationState.COMMITTED.value, PublicationState.AVAILABLE.value]
            ),
        )
        rev_count = (await session.execute(rev_count_stmt)).scalar() or 0

        # Count active/non-deleted stages referencing this checksum
        live_stage_stmt = select(func.count(WeAIStagedObject.id)).where(
            WeAIStagedObject.checksum_sha256 == sha256,
            WeAIStagedObject.stage_status.in_(["STAGED", "VERIFIED", "COMMITTED"]),
        )
        live_stage_count = (await session.execute(live_stage_stmt)).scalar() or 0

        obj_reg = await session.get(WeAIObjectRegistry, sha256)
        total_refs = rev_count + live_stage_count

        if total_refs > 0:
            blobs_preserved += 1
            if obj_reg is not None:
                obj_reg.ref_count = total_refs
                obj_reg.gc_status = "ACTIVE"
        else:
            # Safe to claim and delete physical blob
            if obj_reg is not None:
                obj_reg.ref_count = 0
                obj_reg.gc_status = "DELETE_CLAIMED"
                obj_reg.gc_generation += 1
                await session.flush()

            blob_key = obj_reg.canonical_object_key if obj_reg else f"blobs/{sha256}"
            try:
                scoped_key = store._scoped_key(blob_key)
                local_path = store._local_path(scoped_key)
                if local_path.exists():
                    local_path.unlink()
                    blobs_deleted += 1
                if obj_reg is not None:
                    obj_reg.gc_status = "DELETED"
            except Exception as exc:
                log.warning("Failed to delete abandoned staged blob %s: %s", blob_key, exc)
                if obj_reg is not None:
                    obj_reg.gc_status = "QUARANTINED"

    await session.flush()
    return {
        "stages_cleaned": stages_cleaned,
        "blobs_deleted": blobs_deleted,
        "blobs_preserved": blobs_preserved,
    }
