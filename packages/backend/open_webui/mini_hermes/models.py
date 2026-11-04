from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
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
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    drive_file_id: Mapped[str | None] = mapped_column(String(255))
    drive_mime_type: Mapped[str | None] = mapped_column(String(255))
    drive_version: Mapped[str | None] = mapped_column(String(255))
    office_companion_id: Mapped[str | None] = mapped_column(String(255))
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


class Preview(Base):
    __tablename__ = 'mh_preview'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('mh_document.id'), index=True)
    owner_id: Mapped[str] = mapped_column(String)
    base_revision: Mapped[int] = mapped_column(Integer)
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
