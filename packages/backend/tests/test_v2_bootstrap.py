"""Tests for Agent Runtime V2 fresh bootstrap runner."""

import asyncio
import os
import sys
from uuid import uuid4

import bcrypt
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from weai.persistence.v2.bootstrap import (
    BootstrapConfig,
    DatabaseAlreadyBootstrappedError,
    LegacyV1DatabaseDetectedError,
    bootstrap_v2_database,
)
from weai.persistence.v2.migrations.runner import (
    check_v2_migrations_up_to_date,
    get_v2_current_revision,
    run_v2_migrations,
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def disposable_pg_schema():
    """Fixture providing a completely isolated temporary PostgreSQL schema."""
    db_url = os.getenv("AGENT_RUNTIME_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set AGENT_RUNTIME_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("AGENT_RUNTIME_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    schema_name = f"ar_test_boot_{uuid4().hex}"
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
async def test_bootstrap_v2_database_creates_all_tables_and_seeds(disposable_pg_schema):
    """Bootstrap creates the full release schema, roles, admin, and environment marker."""
    schema_url, schema_name = disposable_pg_schema

    config = BootstrapConfig(
        database_url=schema_url,
        environment_id="development",
        schema_version="v2.0",
        admin_email="testadmin@hermes.test",
        admin_password="SuperSecretPassword123!",
        admin_name="Test Administrator",
    )

    summary = await bootstrap_v2_database(config)

    assert summary["tables_created"] == 104
    assert get_v2_current_revision(schema_url) == "0006_ar_model_tiers"
    assert summary["environment_id"] == "development"
    assert summary["admin_email"] == "testadmin@hermes.test"
    assert len(summary["roles_seeded"]) == 4

    # Verify directly via engine
    engine = create_async_engine(schema_url)
    try:
        async with engine.connect() as conn:
            # 1. Verify tables count
            tables = (
                await conn.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        f"WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE'"
                    )
                )
            ).all()
            ar_tables = [t for t in tables if t.startswith("ar_")]
            assert len(ar_tables) == 104

            # 2. Verify roles
            roles = (await conn.scalars(text("SELECT name FROM ar_roles ORDER BY name"))).all()
            assert sorted(roles) == ["admin", "auditor", "reviewer", "user"]

            # 3. Verify admin user
            admin = (
                await conn.execute(
                    text("SELECT email, name, role, password_hash, is_active FROM ar_users WHERE email = :email"),
                    {"email": "testadmin@hermes.test"},
                )
            ).mappings().fetchone()
            assert admin is not None
            assert admin["name"] == "Test Administrator"
            assert admin["role"] == "admin"
            assert admin["is_active"] is True
            # Verify bcrypt check
            assert bcrypt.checkpw("SuperSecretPassword123!".encode("utf-8"), admin["password_hash"].encode("utf-8"))

            # 4. Verify environment marker
            marker = (
                await conn.execute(
                    text("SELECT key, instance_id, environment_name, schema_generation, safety_token FROM ar_environment_markers")
                )
            ).mappings().fetchone()
            assert marker is not None
            assert marker["key"] == "environment_identity"
            assert marker["environment_name"] == "development"
            assert marker["schema_generation"] == "v2.0"
            assert len(marker["safety_token"]) == 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_refuses_already_bootstrapped_db(disposable_pg_schema):
    """bootstrap_v2_database must fail closed if already bootstrapped."""
    schema_url, _ = disposable_pg_schema

    config = BootstrapConfig(database_url=schema_url, environment_id="development")
    await bootstrap_v2_database(config)

    # Second invocation must raise DatabaseAlreadyBootstrappedError
    with pytest.raises(DatabaseAlreadyBootstrappedError, match="already bootstrapped"):
        await bootstrap_v2_database(config)


@pytest.mark.asyncio
async def test_bootstrap_refuses_legacy_v1_database(disposable_pg_schema):
    """bootstrap_v2_database must fail closed if legacy V1 tables are detected."""
    schema_url, _ = disposable_pg_schema

    # Create a legacy V1 table in the schema
    engine = create_async_engine(schema_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE auth (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255))"))
    finally:
        await engine.dispose()

    config = BootstrapConfig(database_url=schema_url, environment_id="development")
    with pytest.raises(LegacyV1DatabaseDetectedError, match="Legacy V1 tables detected"):
        await bootstrap_v2_database(config)


def test_alembic_v2_migrations_upgrade_and_downgrade(disposable_pg_schema):
    """Alembic V2 migrations can upgrade to head and downgrade cleanly."""
    schema_url, schema_name = disposable_pg_schema

    run_v2_migrations(schema_url, "0001_v2_baseline")
    assert check_v2_migrations_up_to_date(schema_url) is False

    run_v2_migrations(schema_url, "head")

    assert get_v2_current_revision(schema_url) == "0006_ar_model_tiers"
    assert check_v2_migrations_up_to_date(schema_url) is True

    from sqlalchemy import create_engine

    engine = create_engine(schema_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
            assert conn.scalar(text("SELECT 'spreadsheet'::text % 'spreadsheet'::varchar")) is True
            indexes = set(conn.scalars(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = 'ar_document_chunk'"
            ), {'schema': schema_name}))
            assert {'ix_ar_chunk_fts', 'ix_ar_chunk_trgm'} <= indexes
    finally:
        engine.dispose()

    # Downgrade to base
    run_v2_migrations(schema_url, "base")

    assert get_v2_current_revision(schema_url) is None
    assert check_v2_migrations_up_to_date(schema_url) is False


def test_v2_migration_does_not_create_retired_weai_tables(disposable_pg_schema):
    """A fresh release database must contain only supported product tables."""
    schema_url, schema_name = disposable_pg_schema
    run_v2_migrations(schema_url, "head")

    from sqlalchemy import create_engine

    engine = create_engine(schema_url)
    try:
        with engine.connect() as conn:
            tables = set(conn.scalars(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
            ), {"schema": schema_name}))
    finally:
        engine.dispose()

    assert {
        "ar_agent_session", "ar_agent_turn", "ar_agent_event",
        "ar_ow_user", "ar_ow_auth", "ar_ow_chat", "ar_ow_model",
    } <= tables
    assert sum(name.startswith("ar_") for name in tables) == 104
    assert not tables.intersection({
        "ar_ow_app", "ar_ow_automation", "ar_ow_automation_run",
        "ar_ow_calendar", "ar_ow_calendar_event", "ar_ow_calendar_event_attendee",
        "ar_ow_channel", "ar_ow_channel_file", "ar_ow_channel_member",
        "ar_ow_channel_webhook", "ar_ow_comment", "ar_ow_comment_reaction",
        "ar_ow_function", "ar_ow_function_history", "ar_ow_note",
        "ar_ow_pinned_note", "ar_ow_shared_chat",
    })


def test_v2_migration_runner_does_not_silently_repair_schema(disposable_pg_schema):
    """Only a versioned migration may change a deployed schema."""
    schema_url, _ = disposable_pg_schema
    run_v2_migrations(schema_url, "head")

    from sqlalchemy import create_engine

    engine = create_engine(schema_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE ar_ow_model CASCADE"))
        run_v2_migrations(schema_url, "head")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT to_regclass('ar_ow_model')")) is None
    finally:
        engine.dispose()


def test_v2_migration_upgrades_existing_agent_wait_constraint(disposable_pg_schema):
    """An already created V2 database accepts the distinct user-input wait state."""
    schema_url, _ = disposable_pg_schema
    run_v2_migrations(schema_url, "0004_v2_analysis_trace")

    from sqlalchemy import create_engine

    engine = create_engine(schema_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE ar_agent_turn DROP CONSTRAINT ck_agent_turn_status"
            ))
            conn.execute(text(
                "ALTER TABLE ar_agent_turn ADD CONSTRAINT ck_agent_turn_status "
                "CHECK (status IN ('queued', 'running', 'waiting_approval', 'paused', "
                "'completed', 'failed', 'interrupted', 'cancelled'))"
            ))
        run_v2_migrations(schema_url, "head")
        with engine.connect() as conn:
            definition = conn.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'ar_agent_turn'::regclass "
                "AND conname = 'ck_agent_turn_status'"
            ))
        assert "waiting_user_input" in definition
    finally:
        engine.dispose()
