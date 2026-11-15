"""Tests for Derived Index Lifecycle, Version Gating, CAS Swaps, and Invalidation (M2.5)."""

import asyncio
import hashlib
import os
import socket
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.indexing import (
    IndexBuildNotFoundError,
    IndexingError,
    IndexLifecycleManager,
    IndexNotReadyError,
    IndexSchemaIncompatibleError,
    StaleFencingTokenError,
    V2_CHUNKER_VERSION,
    V2_INDEX_SCHEMA_VERSION,
    V2_XLSX_PARSER_VERSION,
    compute_index_manifest_hash,
)
from open_webui.mini_hermes.v2.models import (
    FormatType,
    IndexBuildState,
    MhActiveIndex,
    MhDocument,
    MhDocumentRevision,
    MhIndexBuild,
    utcnow,
)

if sys.platform == "win32":
    try:
        from open_webui.utils.windows_asyncio import install_windows_selector_policy

        install_windows_selector_policy()
    except ImportError:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def disposable_pg_schema():
    """Fixture providing an isolated temporary PostgreSQL schema for integration tests."""
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    host = url.host or "127.0.0.1"
    port = url.port or 5432
    try:
        sock = socket.create_connection((host, port), timeout=0.5)
        sock.close()
    except (OSError, socket.timeout):
        pytest.skip(f"PostgreSQL server at {host}:{port} is offline")

    schema_name = f"mh_test_idx_{uuid.uuid4().hex}"
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


# --- Unit Tests: Manifest Hash and Deterministic Bindings (Offline) ---


def test_index_manifest_hash_determinism():
    """Manifest hash must be 100% deterministic given identical inputs."""
    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    sha256 = "e85ad43cf67e3cb70576e6ad19e0f07452d00a1e9f1c7dc169dc1c9b248248be"

    hash1 = compute_index_manifest_hash(
        document_id=doc_id,
        revision_id=rev_id,
        source_checksum_sha256=sha256,
        parser_version=V2_XLSX_PARSER_VERSION,
        chunker_version=V2_CHUNKER_VERSION,
        schema_version=V2_INDEX_SCHEMA_VERSION,
        chunks_sha256_list=["chunk_a", "chunk_b"],
        anchor_count=5,
    )
    hash2 = compute_index_manifest_hash(
        document_id=doc_id,
        revision_id=rev_id,
        source_checksum_sha256=sha256,
        parser_version=V2_XLSX_PARSER_VERSION,
        chunker_version=V2_CHUNKER_VERSION,
        schema_version=V2_INDEX_SCHEMA_VERSION,
        chunks_sha256_list=["chunk_b", "chunk_a"],  # Order permutation must not change hash
        anchor_count=5,
    )
    assert hash1 == hash2
    assert len(hash1) == 64


def test_index_manifest_hash_detects_mutation():
    """Different chunk, anchor count, or parser version produces distinct manifest hashes."""
    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    sha256 = "e85ad43cf67e3cb70576e6ad19e0f07452d00a1e9f1c7dc169dc1c9b248248be"

    base_hash = compute_index_manifest_hash(
        document_id=doc_id,
        revision_id=rev_id,
        source_checksum_sha256=sha256,
        parser_version=V2_XLSX_PARSER_VERSION,
        chunker_version=V2_CHUNKER_VERSION,
        schema_version=V2_INDEX_SCHEMA_VERSION,
        anchor_count=1,
    )
    diff_hash = compute_index_manifest_hash(
        document_id=doc_id,
        revision_id=rev_id,
        source_checksum_sha256=sha256,
        parser_version="xlsx_canonical_v3.0.0",
        chunker_version=V2_CHUNKER_VERSION,
        schema_version=V2_INDEX_SCHEMA_VERSION,
        anchor_count=1,
    )
    assert base_hash != diff_hash


# --- Integration Tests with Disposable PostgreSQL ---


@pytest.mark.asyncio
async def test_index_build_lifecycle_state_machine(disposable_pg_schema):
    """Transitions: REQUESTED -> RUNNING -> VERIFYING / READY -> ACTIVE -> RETIRED."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    idx_mgr = IndexLifecycleManager(session_factory)

    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    source_sha256 = "a" * 64

    # Setup parent doc and rev records
    async with session_factory() as session:
        async with session.begin():
            doc = MhDocument(
                id=doc_id,
                tenant_id="default",
                name="test.xlsx",
                owner_id="user1",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_type="xlsx",
            )
            session.add(doc)
            await session.flush()
            rev = MhDocumentRevision(
                id=rev_id,
                document_id=doc_id,
                revision_no=1,
                object_key="v2/blobs/" + source_sha256,
                checksum_sha256=source_sha256,
                byte_size=1000,
                committed_by="user1",
            )
            session.add(rev)

    # 1. Create build (REQUESTED)
    build = await idx_mgr.create_index_build(
        document_id=doc_id,
        revision_id=rev_id,
        source_sha256=source_sha256,
        parser_version=V2_XLSX_PARSER_VERSION,
    )
    assert build.build_state == IndexBuildState.REQUESTED.value
    assert build.fencing_token == 1

    # 2. Worker claims build (RUNNING)
    claimed = await idx_mgr.claim_build_for_execution(worker_id="worker_alpha", lease_ttl_seconds=60)
    assert claimed is not None
    assert claimed.id == build.id
    assert claimed.build_state == IndexBuildState.RUNNING.value
    assert claimed.lease_worker_id == "worker_alpha"
    assert claimed.fencing_token == 2

    # 3. Complete verification (READY)
    verified = await idx_mgr.complete_build_verification(
        build_id=build.id,
        worker_id="worker_alpha",
        fencing_token=2,
        manifest_hash="b" * 64,
        coverage_proof={"status": "PARSER_COMPLETE"},
    )
    assert verified.build_state == IndexBuildState.READY.value

    # 4. CAS activation (ACTIVE)
    active = await idx_mgr.activate_index_build_cas(revision_id=rev_id, build_id=build.id)
    assert active.active_build_id == build.id
    assert active.generation == 1

    # Verify lookup
    active_entry, build_entry = await idx_mgr.get_active_index_for_revision(rev_id)
    assert active_entry.active_build_id == build.id
    assert build_entry.id == build.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_index_build_revision_advancement_isolation(disposable_pg_schema):
    """Queries for new unindexed revision return INDEX_NOT_READY without falling back to older revision."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    idx_mgr = IndexLifecycleManager(session_factory)

    doc_id = uuid.uuid4()
    rev1_id = uuid.uuid4()
    rev2_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            doc = MhDocument(
                id=doc_id,
                tenant_id="default",
                name="test.xlsx",
                owner_id="user1",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_type="xlsx",
            )
            session.add(doc)
            await session.flush()
            rev1 = MhDocumentRevision(
                id=rev1_id,
                document_id=doc_id,
                revision_no=1,
                object_key="v2/blobs/" + ("1" * 64),
                checksum_sha256="1" * 64,
                byte_size=1000,
                committed_by="user1",
            )
            rev2 = MhDocumentRevision(
                id=rev2_id,
                document_id=doc_id,
                revision_no=2,
                object_key="v2/blobs/" + ("2" * 64),
                checksum_sha256="2" * 64,
                byte_size=1000,
                committed_by="user1",
            )
            session.add_all([rev1, rev2])

    # Build and activate index for Revision 1
    build1 = await idx_mgr.create_index_build(
        document_id=doc_id,
        revision_id=rev1_id,
        source_sha256="1" * 64,
        parser_version=V2_XLSX_PARSER_VERSION,
    )
    await idx_mgr.claim_build_for_execution("worker1")
    await idx_mgr.complete_build_verification(build1.id, "worker1", 2, "m1" + "0" * 62)
    await idx_mgr.activate_index_build_cas(rev1_id, build1.id)

    # Revision 1 is active
    active1, _ = await idx_mgr.get_active_index_for_revision(rev1_id)
    assert active1.active_build_id == build1.id

    # Revision 2 has no index build yet -> must raise IndexNotReadyError
    with pytest.raises(IndexNotReadyError) as exc_info:
        await idx_mgr.get_active_index_for_revision(rev2_id)
    assert exc_info.value.code == "INDEX_NOT_READY"

    await engine.dispose()


@pytest.mark.asyncio
async def test_index_build_schema_incompatibility_fails_closed(disposable_pg_schema):
    """Build with incompatible schema version fails closed upon query."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    idx_mgr = IndexLifecycleManager(session_factory)

    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            doc = MhDocument(
                id=doc_id,
                tenant_id="default",
                name="test.xlsx",
                owner_id="user1",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_type="xlsx",
            )
            session.add(doc)
            await session.flush()
            rev = MhDocumentRevision(
                id=rev_id,
                document_id=doc_id,
                revision_no=1,
                object_key="v2/blobs/" + ("3" * 64),
                checksum_sha256="3" * 64,
                byte_size=1000,
                committed_by="user1",
            )
            session.add(rev)

    # Incompatible schema version "v1.0"
    build = await idx_mgr.create_index_build(
        document_id=doc_id,
        revision_id=rev_id,
        source_sha256="3" * 64,
        parser_version=V2_XLSX_PARSER_VERSION,
        schema_version="v1.0",
    )
    await idx_mgr.claim_build_for_execution("worker1")
    await idx_mgr.complete_build_verification(build.id, "worker1", 2, "m3" + "0" * 62)

    # Activating an incompatible schema version raises IndexSchemaIncompatibleError
    with pytest.raises(IndexSchemaIncompatibleError) as exc_info:
        await idx_mgr.activate_index_build_cas(rev_id, build.id)
    assert exc_info.value.code == "INDEX_SCHEMA_INCOMPATIBLE"

    await engine.dispose()


@pytest.mark.asyncio
async def test_index_build_supersession_retires_old_build(disposable_pg_schema):
    """When a new build is activated for the same revision, old build transitions to RETIRED."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    idx_mgr = IndexLifecycleManager(session_factory)

    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            doc = MhDocument(
                id=doc_id,
                tenant_id="default",
                name="test.xlsx",
                owner_id="user1",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_type="xlsx",
            )
            session.add(doc)
            await session.flush()
            rev = MhDocumentRevision(
                id=rev_id,
                document_id=doc_id,
                revision_no=1,
                object_key="v2/blobs/" + ("4" * 64),
                checksum_sha256="4" * 64,
                byte_size=1000,
                committed_by="user1",
            )
            session.add(rev)

    # Build 1
    build1 = await idx_mgr.create_index_build(doc_id, rev_id, "4" * 64, V2_XLSX_PARSER_VERSION)
    await idx_mgr.claim_build_for_execution("w1")
    await idx_mgr.complete_build_verification(build1.id, "w1", 2, "m41" + "0" * 61)
    await idx_mgr.activate_index_build_cas(rev_id, build1.id)

    # Build 2 (Upgraded build)
    build2 = await idx_mgr.create_index_build(doc_id, rev_id, "4" * 64, "xlsx_canonical_v2.1.0")
    await idx_mgr.claim_build_for_execution("w2")
    await idx_mgr.complete_build_verification(build2.id, "w2", 2, "m42" + "0" * 61)
    await idx_mgr.activate_index_build_cas(rev_id, build2.id, expected_generation=1)

    # Active index now points to Build 2
    active, current_build = await idx_mgr.get_active_index_for_revision(rev_id)
    assert active.active_build_id == build2.id
    assert active.generation == 2
    assert current_build.id == build2.id

    # Old build1 must be RETIRED
    async with session_factory() as session:
        old_b = await session.get(MhIndexBuild, build1.id)
        assert old_b.build_state == IndexBuildState.RETIRED.value

    await engine.dispose()
