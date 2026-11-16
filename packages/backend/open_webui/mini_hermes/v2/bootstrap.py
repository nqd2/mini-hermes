"""Fresh bootstrap runner for Mini-Hermes V2 persistence.

Initializes an empty PostgreSQL database with the versioned release schema,
seeds baseline roles, creates initial administrator, and writes the immutable
environment identity marker. Fails closed on legacy V1 tables or existing markers.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from typing import Any

import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from open_webui.mini_hermes.v2.db import normalize_v2_db_url
from open_webui.mini_hermes.v2.models import (
    MhEnvironmentMarker,
    MhRole,
    MhUser,
    UserRole,
    utcnow,
)

log = logging.getLogger(__name__)


class DatabaseAlreadyBootstrappedError(RuntimeError):
    """Raised when attempting to bootstrap a database that already has an environment marker."""


class LegacyV1DatabaseDetectedError(RuntimeError):
    """Raised when legacy V1 tables are detected in the target database."""


class BootstrapConfig(BaseModel):
    """Configuration options for database bootstrap."""

    database_url: str = Field(description="Database connection URL")
    environment_id: str = Field(default="development", description="Target environment ID")
    schema_version: str = Field(default="v2.0", description="Schema generation version")
    admin_email: str = Field(default="admin@minihermes.local", description="Initial admin email")
    admin_password: str = Field(default="Admin@12345", description="Initial admin password")
    admin_name: str = Field(default="System Administrator", description="Admin display name")
    object_namespace: str = Field(default="v2/", description="Object storage namespace prefix")
    index_namespace: str = Field(default="mh_v2_indexes", description="Search index namespace")


DEFAULT_ROLES: list[dict[str, Any]] = [
    {
        "name": UserRole.ADMIN.value,
        "description": "Full system administrator with unrestricted access.",
        "permissions": {
            "all": True,
            "document:read": True,
            "document:edit": True,
            "document:approve": True,
            "document:delete": True,
            "admin:access": True,
        },
    },
    {
        "name": UserRole.USER.value,
        "description": "Standard user capable of uploading, editing, and chatting.",
        "permissions": {
            "document:read": True,
            "document:edit": True,
            "chat:access": True,
        },
    },
    {
        "name": UserRole.REVIEWER.value,
        "description": "Reviewer authorized to inspect diffs and approve document revisions.",
        "permissions": {
            "document:read": True,
            "document:approve": True,
            "diff:inspect": True,
        },
    },
    {
        "name": UserRole.AUDITOR.value,
        "description": "Read-only auditor with access to history, outbox, and logs.",
        "permissions": {
            "document:read": True,
            "audit:read": True,
            "outbox:read": True,
        },
    },
]


async def bootstrap_v2_database(
    config: BootstrapConfig,
    engine: AsyncEngine | None = None,
) -> dict[str, Any]:
    """Execute fresh bootstrap on a PostgreSQL database.

    1. Fail-closed check: verify no legacy V1 tables exist.
    2. Check if mh_environment_markers has an active identity.
    3. Apply the frozen versioned PostgreSQL migrations.
    4. Seed default roles (admin, user, reviewer, auditor).
    5. Seed initial admin user with bcrypt password hash.
    6. Insert immutable environment marker in mh_environment_markers.
    """
    normalized_url = normalize_v2_db_url(config.database_url)
    async_engine = engine or create_async_engine(normalized_url, pool_pre_ping=True)
    should_dispose = engine is None

    try:
        # Refuse a populated or legacy database before running any migration.
        async with async_engine.connect() as preflight:
            preexisting_tables = await preflight.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
            legacy_tables = [
                table for table in preexisting_tables
                if not table.startswith("mh_")
                and table not in ("alembic_version", "alembic_version_v2")
            ]
            if legacy_tables:
                raise LegacyV1DatabaseDetectedError(
                    f"Legacy V1 tables detected: {legacy_tables}. "
                    "Mini-Hermes V2 refuses to modify a legacy database."
                )
            if "mh_environment_markers" in preexisting_tables:
                existing_marker = (
                    await preflight.execute(
                        select(MhEnvironmentMarker.environment_name, MhEnvironmentMarker.instance_id).limit(1)
                    )
                ).mappings().fetchone()
                if existing_marker is not None:
                    raise DatabaseAlreadyBootstrappedError(
                        f"Database is already bootstrapped with environment '{existing_marker['environment_name']}' "
                        f"(instance: {existing_marker['instance_id']})."
                    )

        from open_webui.mini_hermes.v2.migrations.runner import run_v2_migrations

        await asyncio.to_thread(run_v2_migrations, normalized_url)

        async with async_engine.begin() as conn:
            # 1. Inspect existing tables for fail-closed checks
            def _check_existing(sync_conn):
                inspector = inspect(sync_conn)
                tables = inspector.get_table_names()
                return tables

            existing_tables = await conn.run_sync(_check_existing)

            # Check for legacy V1 tables
            legacy_tables = [
                t for t in existing_tables
                if not t.startswith("mh_") and t not in ("alembic_version", "alembic_version_v2")
            ]
            if legacy_tables:
                raise LegacyV1DatabaseDetectedError(
                    f"Legacy V1 tables detected: {legacy_tables}. "
                    "Mini-Hermes V2 enforces Greenfield persistence and strictly refuses to "
                    "connect to or modify legacy V1 databases."
                )

            # 2. Check if already bootstrapped
            if "mh_environment_markers" in existing_tables:
                result = await conn.execute(
                    select(
                        MhEnvironmentMarker.environment_name,
                        MhEnvironmentMarker.instance_id,
                    ).limit(1)
                )
                existing_marker = result.mappings().fetchone()
                if existing_marker is not None:
                    raise DatabaseAlreadyBootstrappedError(
                        f"Database is already bootstrapped with environment '{existing_marker['environment_name']}' "
                        f"(instance: {existing_marker['instance_id']})."
                    )

            # 4. Seed default roles
            roles_seeded: list[str] = []
            for r_data in DEFAULT_ROLES:
                stmt = (
                    pg_insert(MhRole)
                    .values(
                        id=uuid.uuid4(),
                        name=r_data["name"],
                        description=r_data["description"],
                        permissions_json=r_data["permissions"],
                        created_at=utcnow(),
                    )
                    .on_conflict_do_nothing(index_elements=["name"])
                )
                await conn.execute(stmt)
                roles_seeded.append(r_data["name"])

            # 5. Seed initial admin user
            admin_id = uuid.uuid4()
            password_bytes = config.admin_password.encode("utf-8")
            hashed_pw = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")

            admin_stmt = (
                pg_insert(MhUser)
                .values(
                    id=admin_id,
                    email=config.admin_email,
                    name=config.admin_name,
                    role=UserRole.ADMIN.value,
                    is_active=True,
                    password_hash=hashed_pw,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                .on_conflict_do_nothing(index_elements=["email"])
            )
            await conn.execute(admin_stmt)

            # 6. Insert immutable environment marker
            instance_id = uuid.uuid4()
            safety_token = secrets.token_hex(32)
            now = utcnow()

            marker_stmt = (
                pg_insert(MhEnvironmentMarker)
                .values(
                    key="environment_identity",
                    instance_id=instance_id,
                    environment_name=config.environment_id,
                    schema_generation=config.schema_version,
                    safety_token=safety_token,
                    object_namespace=config.object_namespace,
                    index_namespace=config.index_namespace,
                    created_at=now,
                    verified_at=now,
                )
            )
            await conn.execute(marker_stmt)

            # 7. Initialize V2 object storage marker
            from open_webui.mini_hermes.v2.storage import V2ObjectStore
            store = V2ObjectStore(
                prefix=config.object_namespace,
                index_namespace=config.index_namespace,
            )
            store.write_storage_marker(
                environment_id=config.environment_id,
                schema_version=config.schema_version,
            )

            log.info(
                "Mini-Hermes V2 database bootstrapped successfully: instance=%s, env=%s",
                instance_id,
                config.environment_id,
            )

            return {
                "instance_id": str(instance_id),
                "environment_id": config.environment_id,
                "schema_version": config.schema_version,
                "safety_token": safety_token,
                "admin_id": str(admin_id),
                "admin_email": config.admin_email,
                "roles_seeded": roles_seeded,
                "tables_created": sum(table.startswith("mh_") for table in existing_tables),
            }
    finally:
        if should_dispose:
            await async_engine.dispose()
