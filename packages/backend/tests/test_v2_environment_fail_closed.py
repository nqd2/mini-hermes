"""Tests for Fail-Closed Startup Validation and Namespace Isolation (M1.3)."""

import asyncio
import os
import sys
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
from open_webui.mini_hermes.v2.storage import (
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
async def disposable_pg_schema():
    """Fixture providing a disposable schema for testing."""
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    schema_name = f"mh_test_env_{uuid4().hex}"
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    schema_url = url.set(query={**dict(url.query), "options": f"-csearch_path={schema_name}"})

    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        yield schema_url.render_as_string(hide_password=False), schema_name
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_startup_validation_succeeds_on_valid_v2_database(disposable_pg_schema):
    """Validation must succeed when the database is properly bootstrapped."""
    schema_url, _ = disposable_pg_schema

    await bootstrap_v2_database(
        BootstrapConfig(database_url=schema_url, environment_id="development")
    )

    result = await check_async_database_environment(schema_url, expected_environment_id="development")
    assert result["valid"] is True
    assert result["environment_name"] == "development"
    assert result["tables_count"] == 49


@pytest.mark.asyncio
async def test_startup_validation_fails_on_legacy_v1_tables(disposable_pg_schema):
    """Validation must fail closed if any legacy V1 table is present."""
    schema_url, _ = disposable_pg_schema

    # Bootstrap V2 first
    await bootstrap_v2_database(
        BootstrapConfig(database_url=schema_url, environment_id="development")
    )

    # Infiltrate a legacy V1 table
    engine = create_async_engine(schema_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
    finally:
        await engine.dispose()

    # Must raise LegacyV1TablesDetectedError
    with pytest.raises(LegacyV1TablesDetectedError, match="Legacy V1 database tables detected"):
        await check_async_database_environment(schema_url, expected_environment_id="development")


@pytest.mark.asyncio
async def test_startup_validation_fails_on_missing_environment_marker(disposable_pg_schema):
    """Validation must fail closed if mh_environment_markers is absent or empty."""
    schema_url, _ = disposable_pg_schema

    # Empty schema without bootstrap
    with pytest.raises(MissingEnvironmentMarkerError, match="Table 'mh_environment_markers' is missing"):
        await check_async_database_environment(schema_url, expected_environment_id="development")

    # Now bootstrap, then wipe the marker
    await bootstrap_v2_database(
        BootstrapConfig(database_url=schema_url, environment_id="development")
    )
    engine = create_async_engine(schema_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM mh_environment_markers;"))
    finally:
        await engine.dispose()

    with pytest.raises(MissingEnvironmentMarkerError, match="No environment marker record found"):
        await check_async_database_environment(schema_url, expected_environment_id="development")


@pytest.mark.asyncio
async def test_startup_validation_fails_on_environment_mismatch(disposable_pg_schema):
    """Validation must fail closed if database environment differs from expected."""
    schema_url, _ = disposable_pg_schema

    # Bootstrap as development
    await bootstrap_v2_database(
        BootstrapConfig(database_url=schema_url, environment_id="development")
    )

    # Attempt to start as production
    with pytest.raises(EnvironmentMismatchError, match="Environment mismatch"):
        await check_async_database_environment(schema_url, expected_environment_id="production")


@pytest.mark.asyncio
async def test_startup_exit_hooks_call_sys_exit_on_failure(disposable_pg_schema, monkeypatch):
    """validate_sync_startup_or_exit and validate_async_startup_or_exit must call sys.exit(1)."""
    schema_url, _ = disposable_pg_schema

    # On empty schema, must exit(1)
    with pytest.raises(SystemExit) as exc_info:
        await validate_async_startup_or_exit(schema_url, expected_environment_id="development")
    assert exc_info.value.code == 1

    with pytest.raises(SystemExit) as exc_info:
        validate_sync_startup_or_exit(schema_url, expected_environment_id="development")
    assert exc_info.value.code == 1


def test_storage_namespace_isolation_and_marker(tmp_path):
    """Verify V2ObjectStore enforces 'v2/' prefix and manages env_marker.json."""
    store = V2ObjectStore(base_dir=tmp_path, prefix="v2/")

    # Put immutable blob
    test_bytes = b"Hello, Mini-Hermes V2!"
    key, sha256 = store.put_immutable(test_bytes)
    assert key.startswith("v2/blobs/")
    assert store.get_bytes(key) == test_bytes

    # Missing marker fails closed
    with pytest.raises(StorageMarkerMissingError, match="missing"):
        store.verify_storage_marker("development")

    # Write marker and verify
    store.write_storage_marker("development")
    marker_info = store.verify_storage_marker("development")
    assert marker_info["environment_id"] == "development"

    # Mismatched environment fails closed
    with pytest.raises(StorageNamespaceViolationError, match="mismatch"):
        store.verify_storage_marker("production")
