"""Domain 5: Task Workers (5 tables).

Tables:
31. mh_document_tasks
32. mh_analysis_jobs
33. mh_summary_tasks
34. mh_export_jobs
35. mh_worker_leases
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


class MhDocumentTask(V2Base):
    """Asynchronous document processing task tracking."""

    __tablename__ = "mh_document_tasks"
    __table_args__ = (
        Index("ix_mh_doc_tasks_type_status", "task_type", "status"),
        Index("ix_mh_doc_tasks_doc_rev", "document_id", "revision_id"),
        Index("ix_mh_doc_tasks_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_doc_tasks_doc_id"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="CASCADE", name="fk_mh_doc_tasks_rev_id"),
        nullable=True,
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhAnalysisJob(V2Base):
    """Long-running analysis execution job record."""

    __tablename__ = "mh_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_mh_analysis_jobs_task"),
        Index("ix_mh_analysis_jobs_doc_status", "document_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_tasks.id", ondelete="CASCADE", name="fk_mh_analysis_jobs_task_id"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_analysis_jobs_doc_id"),
        nullable=False,
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    specification_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    evidence_proof_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    verified_outcome_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhSummaryTask(V2Base):
    """Summary generation task tracking."""

    __tablename__ = "mh_summary_tasks"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_mh_summary_tasks_task"),
        Index("ix_mh_summary_tasks_doc_status", "document_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_tasks.id", ondelete="CASCADE", name="fk_mh_summary_tasks_task_id"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_summary_tasks_doc_id"),
        nullable=False,
    )
    scope_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    structured_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_anchors_json: Mapped[dict | list | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhExportJob(V2Base):
    """Document export job generating standalone artifacts."""

    __tablename__ = "mh_export_jobs"
    __table_args__ = (
        Index("ix_mh_export_jobs_doc_format", "document_id", "target_format"),
        Index("ix_mh_export_jobs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_export_jobs_doc_id"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="CASCADE", name="fk_mh_export_jobs_rev_id"),
        nullable=False,
    )
    target_format: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    exported_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhWorkerLease(V2Base):
    """Fencing token and heartbeat lease for asynchronous background worker nodes."""

    __tablename__ = "mh_worker_leases"
    __table_args__ = (
        Index("ix_mh_worker_leases_node_exp", "worker_node_id", "lease_expires_at"),
        Index("ix_mh_worker_leases_exp", "lease_expires_at"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_tasks.id", ondelete="CASCADE", name="fk_mh_worker_leases_task_id"),
        primary_key=True,
    )
    worker_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
