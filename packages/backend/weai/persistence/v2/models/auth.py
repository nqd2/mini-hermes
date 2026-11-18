"""Domain 6: Auth / RBAC / WeAI Async Entities (14 tables).

Tables:
36. ar_users
37. ar_auth_sessions
38. ar_auth_revoked_session_jtis
39. ar_roles
40. ar_groups
41. ar_group_memberships
42. ar_access_grants
43. ar_folder_nodes
44. ar_chats
45. ar_chat_messages
46. ar_chat_folders
47. ar_models
48. ar_model_params
49. ar_environment_markers
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CHAR,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from weai.persistence.v2.models.base import JSONType, V2Base, utcnow


class WeAIUser(V2Base):
    """Greenfield user principal entity supporting WeAI authentication."""

    __tablename__ = "ar_users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_ar_users_email"),
        Index("ix_ar_users_role", "role"),
        Index("ix_ar_users_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    profile_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    session_stamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settings_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIAuthSession(V2Base):
    """Active user sessions tracking bearer tokens and expiration."""

    __tablename__ = "ar_auth_sessions"
    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_ar_auth_sessions_token_hash"),
        Index("ix_ar_auth_sessions_user", "user_id"),
        Index("ix_ar_auth_sessions_exp", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="CASCADE", name="fk_ar_auth_sessions_user_id"),
        nullable=False,
    )
    session_token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WeAIAuthRevokedSessionJti(V2Base):
    """Durable per-token JTI revocation ledger."""

    __tablename__ = "ar_auth_revoked_session_jtis"
    __table_args__ = (
        Index("ix_ar_revoked_jtis_user", "user_id"),
        Index("ix_ar_revoked_jtis_exp", "expires_at"),
    )

    jti_digest: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="CASCADE", name="fk_ar_revoked_jtis_user_id"),
        nullable=False,
    )
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WeAIRole(V2Base):
    """Role definitions (admin, user, reviewer, auditor) with system permissions."""

    __tablename__ = "ar_roles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_ar_roles_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WeAIGroup(V2Base):
    """User group entity supporting team-based access inheritance."""

    __tablename__ = "ar_groups"
    __table_args__ = (
        Index("ix_ar_groups_name", "name"),
        Index("ix_ar_groups_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="RESTRICT", name="fk_ar_groups_created_by"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIGroupMembership(V2Base):
    """Associative table binding users to groups with group-specific roles."""

    __tablename__ = "ar_group_memberships"
    __table_args__ = (
        Index("ix_ar_group_memberships_user", "user_id"),
        Index("ix_ar_group_memberships_group", "group_id"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_groups.id", ondelete="CASCADE", name="fk_ar_group_memberships_group_id"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="CASCADE", name="fk_ar_group_memberships_user_id"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WeAIAccessGrant(V2Base):
    """Fine-grained discretionary access grants on documents, folders, and workspaces."""

    __tablename__ = "ar_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "grantee_type",
            "grantee_id",
            "permission",
            name="uq_ar_access_grants_grant",
        ),
        Index("ix_ar_access_grants_resource", "resource_type", "resource_id"),
        Index("ix_ar_access_grants_grantee", "grantee_type", "grantee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    grantee_type: Mapped[str] = mapped_column(String(16), nullable=False)
    grantee_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="RESTRICT", name="fk_ar_access_grants_granted_by"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WeAIFolderNode(V2Base):
    """Hierarchical document folder topology supporting recursive permission inheritance."""

    __tablename__ = "ar_folder_nodes"
    __table_args__ = (
        Index("ix_ar_folder_nodes_parent_name", "parent_folder_id", "name"),
        Index("ix_ar_folder_nodes_owner", "owner_id"),
        Index(
            "uq_ar_folder_personal_root",
            "owner_id",
            unique=True,
            postgresql_where=text("is_personal_root = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_folder_nodes.id", ondelete="CASCADE", name="fk_ar_folder_nodes_parent_id"),
        nullable=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="RESTRICT", name="fk_ar_folder_nodes_owner_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_personal_root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIChat(V2Base):
    """Chat session entity bound to authenticated user and conversation engine binding."""

    __tablename__ = "ar_chats"
    __table_args__ = (
        Index("ix_ar_chats_user", "user_id"),
        Index("ix_ar_chats_user_archived", "user_id", "is_archived"),
        Index("ix_ar_chats_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="CASCADE", name="fk_ar_chats_user_id"),
        nullable=False,
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_chat_folders.id", ondelete="SET NULL", name="fk_ar_chats_folder_id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="New Chat")
    meta_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIChatMessage(V2Base):
    """Chat messages with structured tool call payloads, citations, and content annotations."""

    __tablename__ = "ar_chat_messages"
    __table_args__ = (
        Index("ix_ar_chat_messages_chat_created", "chat_id", "created_at"),
        Index("ix_ar_chat_messages_turn", "turn_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_chats.id", ondelete="CASCADE", name="fk_ar_chat_messages_chat_id"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_calls_json: Mapped[list | dict | None] = mapped_column(JSONType, nullable=True)
    citations_json: Mapped[list | dict | None] = mapped_column(JSONType, nullable=True)
    annotations_json: Mapped[list | dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WeAIChatFolder(V2Base):
    """Organizational folders for user chat sessions."""

    __tablename__ = "ar_chat_folders"
    __table_args__ = (
        Index("ix_ar_chat_folders_user_name", "user_id", "name"),
        Index("ix_ar_chat_folders_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ar_users.id", ondelete="CASCADE", name="fk_ar_chat_folders_user_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_expanded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIModel(V2Base):
    """Model registry tracking direct authorized upstream providers."""

    __tablename__ = "ar_models"
    __table_args__ = (
        Index("ix_ar_models_active", "is_active"),
        Index("ix_ar_models_provider", "provider"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIModelParam(V2Base):
    """Model-specific parameters including system prompts and context limits."""

    __tablename__ = "ar_model_params"
    __table_args__ = (
        UniqueConstraint("model_id", name="uq_ar_model_params_model"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("ar_models.id", ondelete="CASCADE", name="fk_ar_model_params_model_id"),
        nullable=False,
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_window_characters: Mapped[int] = mapped_column(Integer, nullable=False, default=95000)
    hyperparams_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WeAIEnvironmentMarker(V2Base):
    """Startup safety marker recording instance UUID, environment identity, and schema generation."""

    __tablename__ = "ar_environment_markers"
    __table_args__ = (
        UniqueConstraint("instance_id", name="uq_ar_env_markers_instance"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, default=uuid.uuid4)
    environment_name: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_generation: Mapped[str] = mapped_column(String(32), nullable=False, default="v2.0")
    safety_token: Mapped[str] = mapped_column(String(128), nullable=False)
    object_namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="v2/")
    index_namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="ar_v2_indexes")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
