"""Domain 1: Storage & Core Documents (10 tables).

Tables:
1.  mh_documents
2.  mh_document_revisions
3.  mh_upload_receipts
4.  mh_ingestion_jobs
5.  mh_index_builds
6.  mh_active_indexes
7.  mh_source_anchors
8.  mh_chunks
9.  mh_object_registry
10. mh_staged_objects
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class MhDocument(V2Base):
    """Root entity for documents."""

    __tablename__ = "mh_documents"
    __table_args__ = (
        Index("ix_mh_documents_tenant_name", "tenant_id", "name"),
        Index("ix_mh_documents_owner", "owner_id"),
        Index("ix_mh_documents_folder", "folder_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_folder_nodes.id", ondelete="SET NULL", name="fk_mh_documents_folder_id"),
        nullable=True,
    )
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    format_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Cyclical foreign key resolved via use_alter=True
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "mh_document_revisions.id",
            use_alter=True,
            name="fk_mh_documents_current_revision_id",
        ),
        nullable=True,
    )
    current_revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class MhDocumentRevision(V2Base):
    """Immutable committed revision record."""

    __tablename__ = "mh_document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_no", name="uq_mh_doc_rev_doc_rev_no"),
        UniqueConstraint("document_id", "id", name="uq_mh_doc_rev_doc_id"),
        Index("ix_mh_doc_rev_sha256", "checksum_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="RESTRICT", name="fk_mh_doc_rev_doc_id"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    commit_status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMMITTED")
    committed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhUploadReceipt(V2Base):
    """Temporary bridge record tracking an uploaded source file."""

    __tablename__ = "mh_upload_receipts"
    __table_args__ = (
        Index("ix_mh_receipts_principal_exp", "principal_id", "expires_at"),
        Index("ix_mh_receipts_ow_file_id", "openwebui_file_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    openwebui_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expected_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    v2_flow_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v2")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhIngestionJob(V2Base):
    """Durable ingestion state machine tracking parsing and indexing."""

    __tablename__ = "mh_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_scope", "idempotency_key", name="uq_mh_ingestion_jobs_idempotency"),
        Index("ix_mh_ingestion_jobs_worker", "lease_worker_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_ingestion_jobs_doc_id"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="SET NULL", name="fk_mh_ingestion_jobs_rev_id"),
        nullable=True,
    )
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    source_state: Mapped[str] = mapped_column(String(32), nullable=False, default="STAGED")
    index_state: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    lease_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class MhIndexBuild(V2Base):
    """Derived index artifact tied to an immutable source revision."""

    __tablename__ = "mh_index_builds"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_id", "id", name="uq_mh_index_builds_doc_rev_id"),
        Index("ix_mh_index_builds_rev_state", "revision_id", "build_state"),
        Index("ix_mh_index_builds_manifest", "manifest_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_index_builds_doc_id"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="CASCADE", name="fk_mh_index_builds_rev_id"),
        nullable=False,
    )
    source_checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    build_state: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    lease_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coverage_proof: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhActiveIndex(V2Base):
    """Authoritative active index build pointer for a document revision."""

    __tablename__ = "mh_active_indexes"
    __table_args__ = (
        UniqueConstraint("revision_id", "active_build_id", name="uq_mh_active_indexes_rev_build"),
        Index("ix_mh_active_indexes_doc_rev", "document_id", "revision_id"),
    )

    revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_document_revisions.id", ondelete="CASCADE", name="fk_mh_active_indexes_rev_id"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_documents.id", ondelete="CASCADE", name="fk_mh_active_indexes_doc_id"),
        nullable=False,
    )
    active_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="RESTRICT", name="fk_mh_active_indexes_build_id"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhSourceAnchor(V2Base):
    """Fine-grained structural coordinates linking content to source locations."""

    __tablename__ = "mh_source_anchors"
    __table_args__ = (
        Index(
            "ix_mh_anchors_build_kind_sheet_page",
            "index_build_id",
            "anchor_kind",
            "sheet_id",
            "page_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_anchors_build_id"),
        nullable=False,
    )
    anchor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sheet_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    col_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    locator_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class MhChunk(V2Base):
    """Searchable text chunks bounded by source anchors."""

    __tablename__ = "mh_chunks"
    __table_args__ = (
        UniqueConstraint("index_build_id", "stable_ordinal", name="uq_mh_chunks_build_ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_build_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_index_builds.id", ondelete="CASCADE", name="fk_mh_chunks_build_id"),
        nullable=False,
    )
    anchor_start_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_source_anchors.id", ondelete="CASCADE", name="fk_mh_chunks_start_anchor_id"),
        nullable=True,
    )
    anchor_end_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_source_anchors.id", ondelete="CASCADE", name="fk_mh_chunks_end_anchor_id"),
        nullable=True,
    )
    stable_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class MhObjectRegistry(V2Base):
    """Content-addressed immutable object storage registry."""

    __tablename__ = "mh_object_registry"
    __table_args__ = (
        UniqueConstraint("canonical_object_key", name="uq_mh_object_registry_key"),
        Index("ix_mh_object_registry_ref_count", "ref_count"),
        Index("ix_mh_object_registry_gc", "gc_status", "gc_generation"),
    )

    checksum_sha256: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    canonical_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="HOT")
    ref_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    gc_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    gc_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhStagedObject(V2Base):
    """Staged object tracking before commit or garbage collection."""

    __tablename__ = "mh_staged_objects"
    __table_args__ = (
        Index("ix_mh_staged_objects_sha256", "checksum_sha256"),
        Index("ix_mh_staged_objects_exp", "expires_at"),
        Index("ix_mh_staged_objects_status_exp", "stage_status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    checksum_sha256: Mapped[str | None] = mapped_column(
        CHAR(64),
        ForeignKey("mh_object_registry.checksum_sha256", ondelete="RESTRICT", name="fk_mh_staged_obj_sha256"),
        nullable=True,
    )
    staged_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stage_status: Mapped[str] = mapped_column(String(32), nullable=False, default="STAGED")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
