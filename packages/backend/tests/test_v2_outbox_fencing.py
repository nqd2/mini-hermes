"""Tests for Transactional Outbox with Concurrency and Lease Fencing (M1.4)."""

import asyncio
import os
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.persistence.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from weai.persistence.v2.models import WeAIOutbox
from weai.persistence.v2.outbox import OutboxDispatcher, insert_outbox_event


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def disposable_pg_schema():
    """Fixture providing a disposable schema for testing."""
    db_url = os.getenv("AGENT_RUNTIME_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set AGENT_RUNTIME_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("AGENT_RUNTIME_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    schema_name = f"ar_test_outbox_{uuid4().hex}"
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
async def test_insert_outbox_event_inside_transaction(disposable_pg_schema):
    """insert_outbox_event must persist outbox event within transaction boundary."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            async with session.begin():
                event = insert_outbox_event(
                    session,
                    event_key="evt_test_1",
                    event_type="document.created",
                    payload={"doc_id": "test-doc-1"},
                    aggregate_type="document",
                    aggregate_id="test-doc-1",
                )
                assert event.event_key == "evt_test_1"

        # Verify persisted
        async with session_factory() as session:
            ev = (
                await session.scalars(select(WeAIOutbox).where(WeAIOutbox.event_key == "evt_test_1"))
            ).one_or_none()
            assert ev is not None
            assert ev.event_type == "document.created"
            assert ev.payload_json == {"doc_id": "test-doc-1"}
            assert ev.published_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_lease_fencing_prevents_stale_worker_publication(disposable_pg_schema):
    """A worker whose lease generation was superseded by another worker cannot mark published."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Seed an event
        async with session_factory() as session:
            async with session.begin():
                insert_outbox_event(
                    session,
                    event_key="evt_fencing_test",
                    event_type="revision.committed",
                    payload={"rev": 1},
                )

        worker_1 = OutboxDispatcher(session_factory, worker_id="worker-1", lease_ttl_seconds=1)
        worker_2 = OutboxDispatcher(session_factory, worker_id="worker-2", lease_ttl_seconds=30)

        # Worker 1 claims
        async with session_factory() as session:
            async with session.begin():
                claimed_1 = await worker_1.claim_batch(session)
        assert len(claimed_1) == 1
        gen_1 = claimed_1[0]["generation"]
        event_id = claimed_1[0]["id"]
        assert gen_1 == 1

        # Simulate Worker 1 lease expiring (TTL was 1 sec)
        await asyncio.sleep(1.2)

        # Worker 2 claims the now-expired event
        async with session_factory() as session:
            async with session.begin():
                claimed_2 = await worker_2.claim_batch(session)
        assert len(claimed_2) == 1
        gen_2 = claimed_2[0]["generation"]
        assert gen_2 == 2  # Fencing token incremented

        # Stale Worker 1 attempts to confirm publication with gen_1
        async with session_factory() as session:
            async with session.begin():
                success_w1 = await worker_1.confirm_published(session, event_id, gen_1)
        # MUST BE FENCED OUT (False)!
        assert success_w1 is False

        # Active Worker 2 confirms with gen_2
        async with session_factory() as session:
            async with session.begin():
                success_w2 = await worker_2.confirm_published(session, event_id, gen_2)
        assert success_w2 is True

        # Verify event is published
        async with session_factory() as session:
            ev = (await session.scalars(select(WeAIOutbox).where(WeAIOutbox.id == event_id))).one()
            assert ev.published_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_workers_skip_locked(disposable_pg_schema):
    """Two concurrent workers claim distinct disjoint batches via SKIP LOCKED."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Seed 4 events
        async with session_factory() as session:
            async with session.begin():
                for i in range(4):
                    insert_outbox_event(
                        session,
                        event_key=f"evt_concurrent_{i}",
                        event_type="test.event",
                        payload={"idx": i},
                    )

        worker_a = OutboxDispatcher(session_factory, worker_id="worker-a", batch_size=2)
        worker_b = OutboxDispatcher(session_factory, worker_id="worker-b", batch_size=2)

        # Worker A claims 2 events
        async with session_factory() as session_a:
            async with session_a.begin():
                batch_a = await worker_a.claim_batch(session_a)

        # Worker B claims 2 events
        async with session_factory() as session_b:
            async with session_b.begin():
                batch_b = await worker_b.claim_batch(session_b)

        assert len(batch_a) == 2
        assert len(batch_b) == 2

        keys_a = {item["event_key"] for item in batch_a}
        keys_b = {item["event_key"] for item in batch_b}

        # Sets must be completely disjoint (zero duplicate claims!)
        assert keys_a.isdisjoint(keys_b)
        assert len(keys_a | keys_b) == 4
    finally:
        await engine.dispose()
