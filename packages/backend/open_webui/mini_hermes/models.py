from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from open_webui.internal.db import Base, JSONField


def now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = 'mh_document'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(255))
    folder_id: Mapped[str | None] = mapped_column(ForeignKey('mh_folder.id'), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    description_status: Mapped[str] = mapped_column(String(24), default='pending')
    description_error: Mapped[str | None] = mapped_column(Text)
    description_attempts: Mapped[int] = mapped_column(Integer, default=0)
    description_next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source_file_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    drive_file_id: Mapped[str | None] = mapped_column(String(255))
    drive_mime_type: Mapped[str | None] = mapped_column(String(255))
    drive_version: Mapped[str | None] = mapped_column(String(255))
    office_companion_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Folder(Base):
    __tablename__ = 'mh_folder'
    __table_args__ = (
        Index('uq_mh_folder_personal_root', 'owner_id', unique=True,
              postgresql_where=text('is_personal_root = true'), sqlite_where=text('is_personal_root = 1')),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('mh_folder.id'), index=True)
    name: Mapped[str] = mapped_column(String(255))
    is_personal_root: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatResource(Base):
    __tablename__ = 'mh_chat_resource'
    __table_args__ = (UniqueConstraint('chat_id', 'document_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(255), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    granted_by: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Revision(Base):
    __tablename__ = 'mh_revision'
    __table_args__ = (UniqueConstraint('document_id', 'number'), UniqueConstraint('preview_id'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    preview_id: Mapped[str | None] = mapped_column(String(36))
    operations: Mapped[list | None] = mapped_column(JSONField)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentChunk(Base):
    """Versioned searchable text with stable source coordinates."""
    __tablename__ = 'mh_document_chunk'
    __table_args__ = (UniqueConstraint('document_id', 'revision', 'ordinal'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer, index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(24), default='text')
    section: Mapped[str | None] = mapped_column(String(512))
    page: Mapped[int | None] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSONField)


class ResourceIndexJob(Base):
    """Durable, idempotent indexing request for one immutable document revision."""
    __tablename__ = 'mh_resource_index_job'
    __table_args__ = (
        UniqueConstraint('document_id', 'revision', name='uq_mh_resource_index_document_revision'),
        Index('ix_mh_resource_index_status_next_attempt', 'status', 'next_attempt_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ResourceTask(Base):
    """Durable checkpoint for long resource operations handled by the existing worker."""
    __tablename__ = 'mh_resource_task'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default='summary')
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    phase: Mapped[str] = mapped_column(String(24), default='chunks')
    next_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    next_group: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=0)
    completed_units: Mapped[int] = mapped_column(Integer, default=0)
    total_units: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[dict | None] = mapped_column(JSONField)
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Preview(Base):
    __tablename__ = 'mh_preview'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    owner_id: Mapped[str] = mapped_column(String)
    base_revision: Mapped[int] = mapped_column(Integer)
    base_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drive_version: Mapped[str | None] = mapped_column(String(255))
    operations: Mapped[list] = mapped_column(JSONField)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    preview_key: Mapped[str | None] = mapped_column(String(1024))
    diff: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SyncJob(Base):
    __tablename__ = 'mh_sync_job'
    __table_args__ = (UniqueConstraint('revision_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey('mh_revision.id'))
    status: Mapped[str] = mapped_column(String(24), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DriveCredential(Base):
    __tablename__ = 'mh_drive_credential'
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
