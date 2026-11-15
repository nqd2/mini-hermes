"""Tests for Dual-Mode Backup, Restore, and 8-Point 100% Parity Verification Harness (M1.5)."""

import asyncio
import os
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.backup import dump_v2_database, restore_v2_database
from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.outbox import insert_outbox_event
from open_webui.mini_hermes.v2.verify_backup import verify_database_parity


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def dual_pg_schemas():
    """Fixture providing two disposable schemas: source and target."""
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    src_schema = f"mh_test_src_{uuid4().hex}"
    tgt_schema = f"mh_test_tgt_{uuid4().hex}"

    admin_engine = create_async_engine(url, pool_pre_ping=True)
    src_url = url.set(query={**dict(url.query), "options": f"-csearch_path={src_schema}"})
    tgt_url = url.set(query={**dict(url.query), "options": f"-csearch_path={tgt_schema}"})

    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{src_schema}"'))
            await conn.execute(text(f'CREATE SCHEMA "{tgt_schema}"'))

        yield (
            src_url.render_as_string(hide_password=False),
            tgt_url.render_as_string(hide_password=False),
            src_schema,
            tgt_schema,
        )
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{src_schema}" CASCADE'))
            await conn.execute(text(f'DROP SCHEMA "{tgt_schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_full_backup_restore_and_8_point_parity_verification(dual_pg_schemas, tmp_path):
    """Verify dump, restore, and 100% 8-point parity verification engine."""
    src_url, tgt_url, src_schema, tgt_schema = dual_pg_schemas

    # 1. Bootstrap source schema
    config = BootstrapConfig(
        database_url=src_url,
        environment_id="development",
        schema_version="v2.0",
        admin_email="backup_admin@local.test",
        admin_password="AdminSecurePassword123!",
    )
    await bootstrap_v2_database(config)

    # 2. Add extra test data to source
    src_engine = create_async_engine(src_url)
    src_sessionmaker = async_sessionmaker(src_engine, expire_on_commit=False)
    try:
        async with src_sessionmaker() as session:
            async with session.begin():
                insert_outbox_event(
                    session,
                    event_key="evt_backup_test_1",
                    event_type="test.backup",
                    payload={"data": "sample payload for verification"},
                )
    finally:
        await src_engine.dispose()

    # 3. Dump source database
    dump_file = tmp_path / "v2_test.dump"
    dump_res = await dump_v2_database(source_url=src_url, output_path=dump_file)
    assert dump_res["success"] is True
    assert dump_file.exists()

    # 4. Restore dump into target schema
    restore_res = await restore_v2_database(target_url=tgt_url, dump_path=dump_file, force=True)
    assert restore_res["status"] == "restored"

    # 5. Run 8-Point Parity Verification Engine
    parity_result = await verify_database_parity(source_url=src_url, target_url=tgt_url)

    # Must pass 100%
    assert parity_result.passed is True, f"Parity failed: {parity_result.discrepancies}"
    assert len(parity_result.discrepancies) == 0
    assert parity_result.tables_verified == 49
    assert parity_result.total_rows_source > 0
    assert parity_result.total_rows_source == parity_result.total_rows_target

    # Verify all 8 individual checks passed
    for check_name, check_val in parity_result.check_details.items():
        assert check_val is True, f"Check '{check_name}' failed!"
