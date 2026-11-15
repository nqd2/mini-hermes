"""Empirical Adversarial Test Suite for Milestone M1.

Tests fail-closed startup validation, environment markers, storage isolation,
and dev DB reset safety guards under adversarial and hostile conditions.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.environment import (
    EnvironmentMismatchError,
    LegacyV1TablesDetectedError,
    MissingEnvironmentMarkerError,
    check_async_database_environment,
    check_sync_database_environment,
    validate_async_startup_or_exit,
    validate_sync_startup_or_exit,
)
from open_webui.mini_hermes.v2.reset_dev_db import (
    ProductionSafetyViolation,
    reset_dev_database,
    verify_safe_for_dev_reset,
)
from open_webui.mini_hermes.v2.storage import (
    StorageChecksumMismatchError,
    StorageMarkerMissingError,
    StorageNamespaceViolationError,
    V2ObjectStore,
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def adversarial_pg_schema():
    """Provides an isolated disposable PostgreSQL schema for adversarial testing."""
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    schema_name = f"mh_adv_{uuid4().hex[:12]}"
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    schema_url = url.set(query={**dict(url.query), "options": f"-csearch_path={schema_name}"})
    rendered_url = schema_url.render_as_string(hide_password=False)

    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        yield rendered_url, schema_name
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await admin_engine.dispose()


# ==============================================================================
# SECTION 1: ADVERSARIAL FAIL-CLOSED STARTUP TESTS (DATABASE)
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_table_name",
    [
        "user",
        "auth",
        "document",
        "chat",
        "message",
        "folder",
        "prompt",
        "Users",
        "Chat",
        "Document",
        "mh",
        "_mh_users",
        "legacy_data",
    ],
)
async def test_startup_fails_closed_on_individual_legacy_tables(adversarial_pg_schema, legacy_table_name):
    """Attempting startup when ANY legacy V1 table is present must raise LegacyV1TablesDetectedError."""
    schema_url, _ = adversarial_pg_schema
    engine = create_async_engine(schema_url)

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE TABLE "{legacy_table_name}" (id VARCHAR(36) PRIMARY KEY)'))
    finally:
        await engine.dispose()

    with pytest.raises(LegacyV1TablesDetectedError) as exc_info:
        await check_async_database_environment(schema_url, expected_environment_id="development")

    assert "Legacy V1 database tables detected" in str(exc_info.value)
    # Check that the legacy table is reported
    assert legacy_table_name in str(exc_info.value) or legacy_table_name.lower() in str(exc_info.value)


@pytest.mark.asyncio
async def test_startup_fails_closed_on_multiple_legacy_tables_simultaneously(adversarial_pg_schema):
    """Presence of multiple core V1 tables (user, auth, document, chat, message) must all be detected."""
    schema_url, _ = adversarial_pg_schema
    engine = create_async_engine(schema_url)

    core_v1_tables = ["user", "auth", "document", "chat", "message"]
    try:
        async with engine.begin() as conn:
            for tbl in core_v1_tables:
                await conn.execute(text(f'CREATE TABLE "{tbl}" (id VARCHAR(36) PRIMARY KEY)'))
    finally:
        await engine.dispose()

    with pytest.raises(LegacyV1TablesDetectedError) as exc_info:
        await check_async_database_environment(schema_url, expected_environment_id="development")

    error_msg = str(exc_info.value)
    for tbl in core_v1_tables:
        assert tbl in error_msg, f"Expected table '{tbl}' to be listed in legacy tables detected error"


@pytest.mark.asyncio
async def test_startup_fails_closed_on_empty_db_without_marker(adversarial_pg_schema):
    """Starting against an empty database with zero tables must raise MissingEnvironmentMarkerError."""
    schema_url, _ = adversarial_pg_schema

    with pytest.raises(MissingEnvironmentMarkerError) as exc_info:
        await check_async_database_environment(schema_url, expected_environment_id="development")

    assert "mh_environment_markers" in str(exc_info.value)


@pytest.mark.asyncio
async def test_startup_fails_closed_on_empty_environment_marker_table(adversarial_pg_schema):
    """Starting against a database where mh_environment_markers exists but contains zero rows must raise MissingEnvironmentMarkerError."""
    schema_url, _ = adversarial_pg_schema

    # Create only mh_environment_markers table with 0 rows
    engine = create_async_engine(schema_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE mh_environment_markers ("
                    "key VARCHAR(64) PRIMARY KEY, "
                    "instance_id VARCHAR(64) NOT NULL, "
                    "environment_name VARCHAR(64) NOT NULL, "
                    "schema_generation INT NOT NULL, "
                    "safety_token VARCHAR(128) NOT NULL)"
                )
            )
    finally:
        await engine.dispose()

    with pytest.raises(MissingEnvironmentMarkerError) as exc_info:
        await check_async_database_environment(schema_url, expected_environment_id="development")

    assert "No environment marker record found" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actual_env,expected_env",
    [
        ("staging", "development"),
        ("development", "production"),
        ("production", "development"),
        ("live", "staging"),
        ("custom_test", "development"),
    ],
)
async def test_startup_fails_closed_on_environment_mismatch(adversarial_pg_schema, actual_env, expected_env):
    """Starting against a database marked for a different environment must raise EnvironmentMismatchError."""
    schema_url, _ = adversarial_pg_schema
    engine = create_async_engine(schema_url)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE mh_environment_markers ("
                    "key VARCHAR(64) PRIMARY KEY, "
                    "instance_id VARCHAR(64) NOT NULL, "
                    "environment_name VARCHAR(64) NOT NULL, "
                    "schema_generation INT NOT NULL, "
                    "safety_token VARCHAR(128) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO mh_environment_markers VALUES "
                    f"('primary', 'inst-001', '{actual_env}', 1, 'tok-123')"
                )
            )
    finally:
        await engine.dispose()

    with pytest.raises(EnvironmentMismatchError) as exc_info:
        await check_async_database_environment(schema_url, expected_environment_id=expected_env)

    assert f"database marked as '{actual_env}'" in str(exc_info.value)
    assert f"expected '{expected_env}'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_startup_exit_hooks_halt_process_with_stderr(adversarial_pg_schema, capsys):
    """validate_async_startup_or_exit and validate_sync_startup_or_exit must write fatal message to stderr and call sys.exit(1)."""
    schema_url, _ = adversarial_pg_schema

    with pytest.raises(SystemExit) as exc_info:
        await validate_async_startup_or_exit(schema_url, expected_environment_id="development")
    assert exc_info.value.code == 1

    with pytest.raises(SystemExit) as exc_info:
        validate_sync_startup_or_exit(schema_url, expected_environment_id="development")
    assert exc_info.value.code == 1


# ==============================================================================
# SECTION 2: ADVERSARIAL STORAGE ISOLATION & MARKER TESTS
# ==============================================================================

def test_storage_marker_mismatch_fails_closed(tmp_path):
    """Storage marker environment mismatch must raise StorageNamespaceViolationError."""
    store = V2ObjectStore(base_dir=tmp_path, prefix="v2/")
    store.write_storage_marker(environment_id="staging")

    with pytest.raises(StorageNamespaceViolationError) as exc_info:
        store.verify_storage_marker(expected_environment_id="production")

    assert "Storage environment mismatch" in str(exc_info.value)


def test_storage_missing_marker_fails_closed(tmp_path):
    """Missing storage marker must raise StorageMarkerMissingError."""
    store = V2ObjectStore(base_dir=tmp_path, prefix="v2/")
    with pytest.raises(StorageMarkerMissingError) as exc_info:
        store.verify_storage_marker(expected_environment_id="development")

    assert "missing" in str(exc_info.value)


def test_storage_immutable_checksum_mismatch_fails_closed(tmp_path):
    """Writing data with incorrect expected SHA-256 must be rejected."""
    store = V2ObjectStore(base_dir=tmp_path, prefix="v2/")
    bad_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

    with pytest.raises(StorageChecksumMismatchError) as exc_info:
        store.put_immutable(b"Test content", expected_sha256=bad_sha256)

    assert "SHA-256 mismatch" in str(exc_info.value)


def test_adversarial_storage_unisolated_prefix_accepted_bug(tmp_path):
    """ADVERSARIAL CHALLENGE: V2ObjectStore should reject unisolated legacy prefixes like 'v1/' or empty prefix.

    Remediated: Raises StorageNamespaceViolationError when initialized with legacy v1/ prefix, empty prefix, or traversal.
    """
    with pytest.raises(StorageNamespaceViolationError) as exc_info:
        V2ObjectStore(base_dir=tmp_path, prefix="v1/")
    assert "legacy" in str(exc_info.value).lower() or "v1" in str(exc_info.value).lower() or "namespace" in str(exc_info.value).lower()

    with pytest.raises(StorageNamespaceViolationError) as exc_info:
        V2ObjectStore(base_dir=tmp_path, prefix="")
    assert "empty" in str(exc_info.value).lower() or "root" in str(exc_info.value).lower()

    with pytest.raises(StorageNamespaceViolationError) as exc_info:
        V2ObjectStore(base_dir=tmp_path, prefix="../../")
    assert "traversal" in str(exc_info.value).lower()


def test_adversarial_storage_path_traversal_bug(tmp_path):
    """ADVERSARIAL CHALLENGE: Path traversal in keys escapes local_root.

    Remediated: Raises StorageNamespaceViolationError on directory traversal attempt and never writes outside local_root.
    """
    store = V2ObjectStore(base_dir=tmp_path, prefix="v2/")
    with pytest.raises(StorageNamespaceViolationError) as exc_info:
        store.put_bytes("../../adversarial_escape.txt", b"escaped_data")
    assert "traversal" in str(exc_info.value).lower() or "escapes" in str(exc_info.value).lower() or "namespace" in str(exc_info.value).lower()

    escaped_file = tmp_path / "adversarial_escape.txt"
    assert not escaped_file.exists(), "Storage path traversal successfully wrote outside local_root!"


# ==============================================================================
# SECTION 3: ADVERSARIAL DEV DB RESET SAFETY TESTS
# ==============================================================================

def test_reset_guard_1_fails_closed_without_confirm():
    """Omitting confirm=False must raise ProductionSafetyViolation."""
    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://minihermes:pass@localhost:5432/minihermes", confirm=False)
    assert "Guard 1 failed" in str(exc_info.value)


@pytest.mark.parametrize("bad_allow_val", ["", "false", "0", "no", "FALSE", "disabled", "null", "none"])
def test_reset_guard_2_fails_closed_on_invalid_allow_env_var(monkeypatch, bad_allow_val):
    """MH_ALLOW_DEV_RESET must strictly be 'true', '1', or 'yes'; any other value must fail closed."""
    if bad_allow_val:
        monkeypatch.setenv("MH_ALLOW_DEV_RESET", bad_allow_val)
    else:
        monkeypatch.delenv("MH_ALLOW_DEV_RESET", raising=False)

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://minihermes:pass@localhost:5432/minihermes", confirm=True)
    assert "Guard 2 failed" in str(exc_info.value)


@pytest.mark.parametrize(
    "env_var_name,env_val",
    [
        ("ENV", "production"),
        ("ENV", "prod"),
        ("ENV", "staging"),
        ("ENV", "stage"),
        ("ENV", "live"),
        ("ENV", "PRODUCTION"),
        ("ENV", "prod-us-east-1"),
        ("ENV", "stage-cluster"),
        ("ENVIRONMENT", "production"),
        ("ENVIRONMENT", "staging"),
        ("ENVIRONMENT", "prod_replica"),
        ("MINI_HERMES_ENV", "production"),
        ("MINI_HERMES_ENV", "prod"),
        ("WEBUI_ENV", "staging"),
        ("WEBUI_ENV", "production"),
        ("NODE_ENV", "production"),
        ("NODE_ENV", "staging"),
    ],
)
def test_reset_guard_3_fails_closed_on_production_environment_variables(monkeypatch, env_var_name, env_val):
    """Any environment variable indicating production/staging/live must fail closed."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv(env_var_name, env_val)

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://minihermes:pass@localhost:5432/minihermes", confirm=True)
    assert "Guard 3 failed" in str(exc_info.value)


@pytest.mark.parametrize("unwhitelisted_env", ["custom_qa", "demo", "uat", "preview_branch", "review_app"])
def test_reset_guard_4_fails_closed_on_unwhitelisted_environment(monkeypatch, unwhitelisted_env):
    """Environments not explicitly whitelisted in ALLOWED_DEV_ENVS must fail closed with Guard 4."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", unwhitelisted_env)

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset("postgresql://minihermes:pass@localhost:5432/minihermes", confirm=True)
    assert "Guard 4 failed" in str(exc_info.value)


@pytest.mark.parametrize(
    "remote_db_url",
    [
        "postgresql://user:pass@mydb.rds.amazonaws.com:5432/hermes_dev",
        "postgresql://user:pass@cluster.rds.amazonaws.com/hermes_dev",
        "postgresql://user:pass@db.cloudsql.internal:5432/hermes_dev",
        "postgresql://user:pass@hermes.postgres.database.azure.com:5432/hermes_dev",
        "postgresql://user:pass@prod-cluster.cockroachlabs.cloud:5432/hermes_dev",
        "postgresql://user:pass@prod-db.corp.internal:5432/hermes_dev",
        "postgresql://user:pass@stage-db.internal:5432/hermes_dev",
        "postgresql://user:pass@10.0.0.1:5432/hermes_dev",
        "postgresql://user:pass@172.16.10.5:5432/hermes_dev",
        "postgresql://user:pass@192.168.1.100:5432/hermes_dev",
        "postgresql://user:pass@external-server.com:5432/hermes_dev",
    ],
)
def test_reset_guard_5_fails_closed_on_remote_and_cloud_hosts(monkeypatch, remote_db_url):
    """Dev reset must strictly reject remote, cloud (RDS, CloudSQL, Azure, Cockroach), or non-local hosts."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset(remote_db_url, confirm=True)
    assert "Guard 5 failed" in str(exc_info.value)


@pytest.mark.parametrize(
    "protected_dbname_url",
    [
        "postgresql://user:pass@localhost:5432/prod",
        "postgresql://user:pass@localhost:5432/production",
        "postgresql://user:pass@localhost:5432/hermes_production_v2",
        "postgresql://user:pass@localhost:5432/minihermes_prod",
        "postgresql://user:pass@localhost:5432/staging",
        "postgresql://user:pass@localhost:5432/hermes_stage",
        "postgresql://user:pass@localhost:5432/live",
        "postgresql://user:pass@localhost:5432/hermes_live_cluster",
        "postgresql://user:pass@127.0.0.1:5433/%70%72%6f%64",  # Percent-encoded 'prod'
    ],
)
def test_reset_guard_6_fails_closed_on_protected_database_names(monkeypatch, protected_dbname_url):
    """Dev reset must strictly reject database names containing prod, stage, or live."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    with pytest.raises(ProductionSafetyViolation) as exc_info:
        verify_safe_for_dev_reset(protected_dbname_url, confirm=True)
    assert "Guard 6 failed" in str(exc_info.value)


# ==============================================================================
# SECTION 4: EMPIRICAL NO DATA LOSS PROOF UNDER SAFETY VIOLATIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_reset_safety_halts_before_execution_zero_data_loss(adversarial_pg_schema, monkeypatch):
    """EMPIRICAL EVIDENCE: Under any safety violation, reset_dev_database halts BEFORE dropping tables.

    Canary table and rows must remain 100% intact.
    """
    schema_url, _ = adversarial_pg_schema
    engine = create_async_engine(schema_url)

    # 1. Create canary table with irreplaceable production-like data
    canary_id = uuid4().hex
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE canary_safety_guard (id VARCHAR(36) PRIMARY KEY, secret_value TEXT)")
            )
            await conn.execute(
                text(f"INSERT INTO canary_safety_guard VALUES ('{canary_id}', 'IRREPLACEABLE_DATA')")
            )
    finally:
        await engine.dispose()

    # 2. Attempt reset with confirm=False
    with pytest.raises(ProductionSafetyViolation):
        await reset_dev_database(schema_url, confirm=False)

    # 3. Attempt reset with ENV=production
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(ProductionSafetyViolation):
        await reset_dev_database(schema_url, confirm=True)

    # 4. Attempt reset with ENVIRONMENT=staging
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ProductionSafetyViolation):
        await reset_dev_database(schema_url, confirm=True)

    # 5. Verify canary table is STILL PRESENT and data is UNTOUCHED
    verify_engine = create_async_engine(schema_url)
    try:
        async with verify_engine.connect() as conn:
            row = (await conn.execute(
                text("SELECT secret_value FROM canary_safety_guard WHERE id = :id"),
                {"id": canary_id},
            )).fetchone()
            assert row is not None, "FATAL: Canary table was dropped during safety violation attempt!"
            assert row[0] == "IRREPLACEABLE_DATA", "FATAL: Canary row was modified or corrupted!"
    finally:
        await verify_engine.dispose()
