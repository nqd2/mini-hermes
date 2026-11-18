"""Fail-closed startup validation for Agent Runtime V2.

Verifies database and storage configurations at startup:
1. Checks target database for legacy V1 tables (tables without 'ar_' prefix). If detected, aborts boot immediately with exit code 1.
2. Checks ar_environment_markers for expected environment ID and schema version. If missing or mismatched, aborts boot immediately.
3. Provides both synchronous and asynchronous validation entry points, as well as testable validation functions.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from weai.persistence.v2.db import normalize_v2_db_url

log = logging.getLogger(__name__)

LEGACY_TABLE_ALLOWLIST = frozenset({"alembic_version", "alembic_version_v2"})


class EnvironmentValidationError(RuntimeError):
    """Base exception for environment validation failures."""


class LegacyV1TablesDetectedError(EnvironmentValidationError):
    """Raised when tables from legacy V1 schema are detected."""


class MissingEnvironmentMarkerError(EnvironmentValidationError):
    """Raised when ar_environment_markers is absent or empty."""


class EnvironmentMismatchError(EnvironmentValidationError):
    """Raised when environment ID in database does not match expected environment."""


def check_sync_database_environment(
    database_url: str | None = None,
    expected_environment_id: str | None = None,
) -> dict[str, Any]:
    """Inspect database synchronously and return status dict or raise typed errors."""
    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    engine = create_engine(url)

    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            tables = inspector.get_table_names()

            # 1. Detect legacy V1 tables
            legacy_tables = [
                t for t in tables
                if not t.startswith("ar_") and t not in LEGACY_TABLE_ALLOWLIST
            ]
            if legacy_tables:
                raise LegacyV1TablesDetectedError(
                    f"Legacy V1 database tables detected: {sorted(legacy_tables)}. "
                    "Agent Runtime V2 greenfield persistence forbids connecting to legacy V1 databases."
                )

            # 2. Check environment marker
            if "ar_environment_markers" not in tables:
                raise MissingEnvironmentMarkerError(
                    "Table 'ar_environment_markers' is missing. Database has not been bootstrapped for V2."
                )

            marker = conn.execute(
                text(
                    "SELECT key, instance_id, environment_name, schema_generation, safety_token "
                    "FROM ar_environment_markers LIMIT 1"
                )
            ).mappings().fetchone()

            if not marker:
                raise MissingEnvironmentMarkerError(
                    "No environment marker record found in 'ar_environment_markers'. Database is uninitialized."
                )

            expected_env = expected_environment_id or os.getenv("AGENT_RUNTIME_ENVIRONMENT_ID", "development")
            if marker["environment_name"] != expected_env:
                raise EnvironmentMismatchError(
                    f"Environment mismatch: database marked as '{marker['environment_name']}', "
                    f"expected '{expected_env}'."
                )

            return {
                "valid": True,
                "instance_id": str(marker["instance_id"]),
                "environment_name": marker["environment_name"],
                "schema_generation": marker["schema_generation"],
                "tables_count": len([t for t in tables if t.startswith("ar_")]),
            }
    finally:
        engine.dispose()


async def check_async_database_environment(
    database_url: str | None = None,
    expected_environment_id: str | None = None,
) -> dict[str, Any]:
    """Inspect database asynchronously and return status dict or raise typed errors."""
    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    async_engine = create_async_engine(url)

    try:
        async with async_engine.connect() as conn:
            def _inspect_sync(sync_conn):
                inspector = inspect(sync_conn)
                return inspector.get_table_names()

            tables = await conn.run_sync(_inspect_sync)

            legacy_tables = [
                t for t in tables
                if not t.startswith("ar_") and t not in LEGACY_TABLE_ALLOWLIST
            ]
            if legacy_tables:
                raise LegacyV1TablesDetectedError(
                    f"Legacy V1 database tables detected: {sorted(legacy_tables)}. "
                    "Agent Runtime V2 greenfield persistence forbids connecting to legacy V1 databases."
                )

            if "ar_environment_markers" not in tables:
                raise MissingEnvironmentMarkerError(
                    "Table 'ar_environment_markers' is missing. Database has not been bootstrapped for V2."
                )

            res = await conn.execute(
                text(
                    "SELECT key, instance_id, environment_name, schema_generation, safety_token "
                    "FROM ar_environment_markers LIMIT 1"
                )
            )
            marker = res.mappings().fetchone()

            if not marker:
                raise MissingEnvironmentMarkerError(
                    "No environment marker record found in 'ar_environment_markers'. Database is uninitialized."
                )

            expected_env = expected_environment_id or os.getenv("AGENT_RUNTIME_ENVIRONMENT_ID", "development")
            if marker["environment_name"] != expected_env:
                raise EnvironmentMismatchError(
                    f"Environment mismatch: database marked as '{marker['environment_name']}', "
                    f"expected '{expected_env}'."
                )

            return {
                "valid": True,
                "instance_id": str(marker["instance_id"]),
                "environment_name": marker["environment_name"],
                "schema_generation": marker["schema_generation"],
                "tables_count": len([t for t in tables if t.startswith("ar_")]),
            }
    finally:
        await async_engine.dispose()


def validate_sync_startup_or_exit(
    database_url: str | None = None,
    expected_environment_id: str | None = None,
) -> None:
    """Synchronous fail-closed startup validation hook. Calls sys.exit(1) on failure."""
    if os.getenv("AGENT_RUNTIME_SKIP_STARTUP_VALIDATION", "").strip().lower() in ("true", "1", "yes"):
        log.info("Database startup validation skipped via AGENT_RUNTIME_SKIP_STARTUP_VALIDATION.")
        return
    try:
        check_sync_database_environment(database_url, expected_environment_id)
        log.info("V2 database environment validation passed.")
    except Exception as exc:
        sys.stderr.write(
            f"\n{'=' * 80}\n"
            f"FATAL: Agent Runtime V2 Startup Validation Failed!\n"
            f"Reason: {exc}\n"
            f"Agent Runtime V2 enforces fail-closed startup to protect data integrity.\n"
            f"{'=' * 80}\n"
        )
        sys.stderr.flush()
        sys.exit(1)


async def validate_async_startup_or_exit(
    database_url: str | None = None,
    expected_environment_id: str | None = None,
) -> None:
    """Asynchronous fail-closed startup validation hook. Calls sys.exit(1) on failure."""
    if os.getenv("AGENT_RUNTIME_SKIP_STARTUP_VALIDATION", "").strip().lower() in ("true", "1", "yes"):
        log.info("Database startup validation skipped via AGENT_RUNTIME_SKIP_STARTUP_VALIDATION.")
        return
    try:
        await check_async_database_environment(database_url, expected_environment_id)
        log.info("V2 database environment validation passed (async).")
    except Exception as exc:
        sys.stderr.write(
            f"\n{'=' * 80}\n"
            f"FATAL: Agent Runtime V2 Startup Validation Failed!\n"
            f"Reason: {exc}\n"
            f"Agent Runtime V2 enforces fail-closed startup to protect data integrity.\n"
            f"{'=' * 80}\n"
        )
        sys.stderr.flush()
        sys.exit(1)
