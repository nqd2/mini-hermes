"""Domain 4: AgentRuntime Engine (8 tables).

Tables:
23. mh_conversation_engine_bindings
24. mh_run_bindings
25. mh_runs
26. mh_run_events
27. mh_tool_invocations
28. mh_checkpoints
29. mh_agent_artifacts
30. mh_execution_leases
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


class MhConversationEngineBinding(V2Base):
    """Pins each conversation to an immutable runtime engine type and version."""

    __tablename__ = "mh_conversation_engine_bindings"
    __table_args__ = (
        Index("ix_mh_conv_engine_type_ver", "engine_type", "engine_version"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    engine_type: Mapped[str] = mapped_column(String(96), nullable=False, default="mini-hermes.agent_runtime")
    engine_version: Mapped[str] = mapped_column(String(96), nullable=False, default="v2")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhRunBinding(V2Base):
    """Binds each execution run to the matching conversation engine version and frozen snapshots."""

    __tablename__ = "mh_run_bindings"
    __table_args__ = (
        UniqueConstraint("run_id", "conversation_id", name="uq_mh_run_bindings_run_conv"),
        Index("ix_mh_run_bindings_conv", "conversation_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_conversation_engine_bindings.conversation_id", ondelete="CASCADE", name="fk_mh_run_bindings_conv_id"),
        nullable=False,
    )
    engine_type: Mapped[str] = mapped_column(String(96), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(96), nullable=False)
    tool_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhRun(V2Base):
    """Tracks top-level execution runs across lifecycle states."""

    __tablename__ = "mh_runs"
    __table_args__ = (
        Index("ix_mh_runs_owner_status", "owner_id", "status"),
        Index("ix_mh_runs_conv_status", "conversation_id", "status"),
        Index("ix_mh_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_conversation_engine_bindings.conversation_id", ondelete="CASCADE", name="fk_mh_runs_conv_id"),
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    error_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhRunEvent(V2Base):
    """Monotonically sequenced durable event log per run."""

    __tablename__ = "mh_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_mh_run_events_run_seq"),
        Index("ix_mh_run_events_run_seq", "run_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_runs.id", ondelete="CASCADE", name="fk_mh_run_events_run_id"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhToolInvocation(V2Base):
    """Durable tool invocation ledger with quarantine flags for ambiguous outcomes."""

    __tablename__ = "mh_tool_invocations"
    __table_args__ = (
        UniqueConstraint("run_id", "idempotency_key", name="uq_mh_tool_inv_run_key"),
        UniqueConstraint("run_id", "call_id", name="uq_mh_tool_inv_run_call"),
        Index("ix_mh_tool_inv_status", "status"),
        Index("ix_mh_tool_inv_quarantine", "quarantined"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_runs.id", ondelete="CASCADE", name="fk_mh_tool_inv_run_id"),
        nullable=False,
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    arguments_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MhCheckpoint(V2Base):
    """Durable execution checkpoints containing state graph and memory frames."""

    __tablename__ = "mh_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_mh_checkpoints_run_ver"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_runs.id", ondelete="CASCADE", name="fk_mh_checkpoints_run_id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_graph_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    memory_frame_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhAgentArtifact(V2Base):
    """Large tool outputs stored as checksummed artifacts."""

    __tablename__ = "mh_agent_artifacts"
    __table_args__ = (
        Index("ix_mh_agent_artifacts_sha256", "checksum_sha256"),
        Index("ix_mh_agent_artifacts_run", "run_id"),
        Index("ix_mh_agent_artifacts_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_runs.id", ondelete="CASCADE", name="fk_mh_agent_artifacts_run_id"),
        nullable=False,
    )
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    bounded_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MhExecutionLease(V2Base):
    """Distributed worker lease and heartbeat fencing for execution runners."""

    __tablename__ = "mh_execution_leases"
    __table_args__ = (
        Index("ix_mh_exec_leases_worker_exp", "worker_id", "expires_at"),
        Index("ix_mh_exec_leases_exp", "expires_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("mh_runs.id", ondelete="CASCADE", name="fk_mh_exec_leases_run_id"),
        primary_key=True,
    )
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
