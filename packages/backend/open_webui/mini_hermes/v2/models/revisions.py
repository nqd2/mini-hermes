"""Domain 3: Revision & Approvals (5 tables).

Tables:
18. mh_edit_previews
19. mh_document_approvals
20. mh_idempotency_records
21. mh_outbox
22. mh_compensating_revisions
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CHAR,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from open_webui.mini_hermes.v2.models.base import JSONType, V2Base, utcnow


class MhEditPreview(V2Base):
    """Holds generated diff preview, canonical patch hash, and staged object with 15m TTL."""

    __tablename__ = "mh_edit_previews"
    __table_args__ = (
        Index("ix_mh_edit_previews_patch_hash", "canonical_patch_hash"),
        Index("ix_mh_edit_previews_exp", "expires_at"),
        Index("ix_mh_edit_previews_doc_base", "document_id", "base_revision_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_edit_previews_doc_id"),
        nullable=False,
    )
    base_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="RESTRICT", name="fk_mh_edit_previews_base_rev_id"),
        nullable=False,
    )
    base_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    staged_object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    canonical_patch_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    operations_json: Mapped[list | dict] = mapped_column(JSONType, nullable=False)
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhDocumentApproval(V2Base):
    """Records human approval of an exact patch hash and base revision."""

    __tablename__ = "mh_document_approvals"
    __table_args__ = (
        UniqueConstraint("preview_id", name="uq_mh_doc_approvals_preview"),
        Index("ix_mh_doc_approvals_user_exp", "approver_user_id", "expires_at"),
        Index("ix_mh_doc_approvals_doc_status", "document_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    preview_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_edit_previews.id", ondelete="RESTRICT", name="fk_mh_doc_approvals_prev_id"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_doc_approvals_doc_id"),
        nullable=False,
    )
    base_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_patch_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    approver_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhIdempotencyRecord(V2Base):
    """Enforces idempotency for mutation endpoints."""

    __tablename__ = "mh_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "operation_kind",
            "idempotency_key",
            name="uq_mh_idempotency_key",
        ),
        Index("ix_mh_idempotency_exp", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    response_status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_response_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhOutbox(V2Base):
    """Transactional outbox table ensuring reliable event publishing with lease fencing."""

    __tablename__ = "mh_outbox"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_mh_outbox_event_key"),
        Index("ix_mh_outbox_type_pub", "event_type", "published_at"),
        Index("ix_mh_outbox_dispatch", "published_at", "lease_expires_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    aggregate_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    lease_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhCompensatingRevision(V2Base):
    """Records compensating rollback revisions reverting faulty commits."""

    __tablename__ = "mh_compensating_revisions"
    __table_args__ = (
        Index("ix_mh_comp_rev_doc_no", "document_id", "compensating_revision_no"),
        Index("ix_mh_comp_rev_faulty", "faulty_revision_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_comp_rev_doc_id"),
        nullable=False,
    )
    faulty_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="RESTRICT", name="fk_mh_comp_rev_faulty_id"),
        nullable=False,
    )
    compensating_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="RESTRICT", name="fk_mh_comp_rev_comp_id"),
        nullable=False,
    )
    faulty_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    compensating_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    rollback_reason: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
