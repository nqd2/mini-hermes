"""Durable state for the canonical agent runtime."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from open_webui.internal.db import Base, JSONField

from .contracts import CURRENT_ENGINE_TYPE, CURRENT_ENGINE_VERSION


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class AgentSession(Base):
    __tablename__ = 'agent_session'
    __table_args__ = (
        UniqueConstraint('owner_id', 'chat_id', name='uq_agent_session_owner_chat'),
        Index('ix_agent_session_owner_updated', 'owner_id', 'updated_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    parent_session_id: Mapped[str | None] = mapped_column(
        ForeignKey('agent_session.id', ondelete='SET NULL'), nullable=True, index=True
    )
    engine_type: Mapped[str] = mapped_column(String(96), nullable=False, default=CURRENT_ENGINE_TYPE)
    engine_version: Mapped[str] = mapped_column(String(96), nullable=False, default=CURRENT_ENGINE_VERSION)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='active')
    next_turn_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_state: Mapped[dict | None] = mapped_column(JSONField, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AgentTurn(Base):
    __tablename__ = 'agent_turn'
    __table_args__ = (
        UniqueConstraint('session_id', 'sequence', name='uq_agent_turn_session_sequence'),
        UniqueConstraint('session_id', 'source_message_id', name='uq_agent_turn_session_source_message'),
        UniqueConstraint('session_id', 'idempotency_key', name='uq_agent_turn_session_idempotency'),
        Index('ix_agent_turn_session_chat_message', 'session_id', 'chat_message_id'),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'paused', 'completed', 'failed', 'interrupted', 'cancelled')",
            name='ck_agent_turn_status',
        ),
        Index('ix_agent_turn_claim', 'status', 'created_at', 'lease_until'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey('agent_session.id', ondelete='CASCADE'), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='queued', index=True)
    request_payload: Mapped[dict] = mapped_column(JSONField, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_type: Mapped[str] = mapped_column(String(96), nullable=False, default=CURRENT_ENGINE_TYPE)
    engine_version: Mapped[str] = mapped_column(String(96), nullable=False, default=CURRENT_ENGINE_VERSION)
    engine_run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    engine_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_contract_version: Mapped[str] = mapped_column(String(255), nullable=False, default='pending')
    skill_snapshot_version: Mapped[str] = mapped_column(String(255), nullable=False, default='pending')
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    user_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[dict | None] = mapped_column(JSONField, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEvent(Base):
    __tablename__ = 'agent_event'
    __table_args__ = (
        UniqueConstraint('turn_id', 'sequence', name='uq_agent_event_turn_sequence'),
        Index('ix_agent_event_outbox', 'published_at', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey('agent_session.id', ondelete='CASCADE'), nullable=False, index=True
    )
    turn_id: Mapped[str] = mapped_column(ForeignKey('agent_turn.id', ondelete='CASCADE'), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONField, nullable=False)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentCheckpoint(Base):
    __tablename__ = 'agent_checkpoint'
    __table_args__ = (
        UniqueConstraint('turn_id', 'version', name='uq_agent_checkpoint_turn_version'),
        Index('ix_agent_checkpoint_session_created', 'session_id', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey('agent_session.id', ondelete='CASCADE'), nullable=False, index=True
    )
    turn_id: Mapped[str] = mapped_column(ForeignKey('agent_turn.id', ondelete='CASCADE'), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default='turn')
    data: Mapped[dict] = mapped_column(JSONField, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentToolInvocation(Base):
    __tablename__ = 'agent_tool_invocation'
    __table_args__ = (
        UniqueConstraint('turn_id', 'call_id', name='uq_agent_tool_turn_call'),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', 'failed', 'outcome_unknown')",
            name='ck_agent_tool_status',
        ),
        Index('ix_agent_tool_turn_status', 'turn_id', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey('agent_session.id', ondelete='CASCADE'), nullable=False, index=True
    )
    turn_id: Mapped[str] = mapped_column(ForeignKey('agent_turn.id', ondelete='CASCADE'), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONField, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='pending', index=True)
    retry_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_data: Mapped[dict | list | str | None] = mapped_column(JSONField, nullable=True)
    result_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONField, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentArtifact(Base):
    __tablename__ = 'agent_artifact'
    __table_args__ = (
        UniqueConstraint('object_key', name='uq_agent_artifact_object_key'),
        Index('ix_agent_artifact_owner_created', 'owner_id', 'created_at'),
        Index('ix_agent_artifact_turn', 'turn_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey('agent_session.id', ondelete='SET NULL'), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey('agent_turn.id', ondelete='SET NULL'), nullable=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONField, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentFileContent(Base):
    """Verified object-store copy of extracted file text with sparse seek indexes."""

    __tablename__ = 'agent_file_content'
    __table_args__ = (Index('ix_agent_file_content_owner_created', 'owner_id', 'created_at'),)

    file_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    char_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_index: Mapped[list] = mapped_column(JSONField, nullable=False)
    char_index: Mapped[list] = mapped_column(JSONField, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
