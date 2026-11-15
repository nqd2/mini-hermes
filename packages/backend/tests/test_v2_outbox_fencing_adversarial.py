"""Adversarial stress and edge-case tests for Transactional Outbox with Concurrency and Lease Fencing."""

import asyncio
import os
import sys
import uuid
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.models import MhOutbox, utcnow
from open_webui.mini_hermes.v2.outbox import OutboxDispatcher, insert_outbox_event


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

    schema_name = f"mh_test_adv_outbox_{uuid4().hex}"
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
async def test_high_concurrency_outbox_claim_stress(disposable_pg_schema):
    """Stress test: 10 concurrent worker tasks competing for 50 pending outbox events.

    Guarantees:
    - Every claim is disjoint (zero duplicate claims across workers).
    - Exactly 50 events claimed in total across all workers.
    - Each event has generation == 1 and delivery_attempts == 1.
    """
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url, pool_size=20, max_overflow=10)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    num_events = 50
    num_workers = 10
    batch_size = 5

    try:
        # 1. Seed 50 events in a single transaction
        async with session_factory() as session:
            async with session.begin():
                for i in range(num_events):
                    insert_outbox_event(
                        session,
                        event_key=f"stress_evt_{i:03d}",
                        event_type="test.stress",
                        payload={"index": i, "token": uuid4().hex},
                    )

        # 2. Spawn 10 concurrent workers competing to claim batches simultaneously
        workers = [
            OutboxDispatcher(session_factory, worker_id=f"worker-{w}", batch_size=batch_size, lease_ttl_seconds=60)
            for w in range(num_workers)
        ]

        async def worker_claim_task(dispatcher: OutboxDispatcher) -> list[dict]:
            claimed_all = []
            while True:
                async with session_factory() as session:
                    async with session.begin():
                        batch = await dispatcher.claim_batch(session)
                        if not batch:
                            break
                        claimed_all.extend(batch)
            return claimed_all

        # Run all workers concurrently
        results = await asyncio.gather(*[worker_claim_task(w) for w in workers])

        # 3. Analyze claim results
        all_claimed_keys = []
        all_claimed_ids = []

        for w_idx, batch in enumerate(results):
            for item in batch:
                all_claimed_keys.append(item["event_key"])
                all_claimed_ids.append(item["id"])
                # Fencing token should be 1
                assert item["generation"] == 1
                assert item["attempts"] == 1

        # Check total count
        assert len(all_claimed_ids) == num_events, (
            f"Expected {num_events} total claims, got {len(all_claimed_ids)}"
        )

        # Check zero duplicates (disjoint sets)
        unique_keys = set(all_claimed_keys)
        unique_ids = set(all_claimed_ids)
        assert len(unique_keys) == num_events, "Duplicate event keys claimed across workers!"
        assert len(unique_ids) == num_events, "Duplicate event IDs claimed across workers!"

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_worker_fencing_exhaustive_matrix(disposable_pg_schema):
    """Exhaustive fencing matrix:

    1. Worker A claims event with generation 1 (TTL=1s).
    2. Worker B cannot claim before TTL expiry.
    3. Worker A's lease expires; Worker B claims with generation 2.
    4. Stale Worker A cannot confirm publication with generation 1 (returns False).
    5. Stale Worker A cannot update failure/lease with generation 1.
    6. Stale Worker A cannot confirm publication using generation 2 because worker_id differs.
    7. Active Worker B successfully confirms publication with generation 2.
    8. Attempting to confirm already published event returns False.
    """
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Seed test event
        event_key = f"fence_matrix_{uuid4().hex[:8]}"
        async with session_factory() as session:
            async with session.begin():
                insert_outbox_event(
                    session,
                    event_key=event_key,
                    event_type="test.fencing",
                    payload={"secure": True},
                )

        worker_a = OutboxDispatcher(session_factory, worker_id="worker-A", lease_ttl_seconds=1)
        worker_b = OutboxDispatcher(session_factory, worker_id="worker-B", lease_ttl_seconds=60)

        # Step 1: Worker A claims (gen 1)
        async with session_factory() as session:
            async with session.begin():
                claimed_a = await worker_a.claim_batch(session)
        assert len(claimed_a) == 1
        ev_id = claimed_a[0]["id"]
        assert claimed_a[0]["generation"] == 1

        # Step 2: Worker B cannot claim while Worker A's lease is active
        async with session_factory() as session:
            async with session.begin():
                claimed_b_premature = await worker_b.claim_batch(session)
        assert len(claimed_b_premature) == 0, "Worker B claimed an event before Worker A's lease expired!"

        # Step 3: Wait for Worker A's lease to expire
        await asyncio.sleep(1.2)

        # Worker B claims the expired event (gen 2)
        async with session_factory() as session:
            async with session.begin():
                claimed_b = await worker_b.claim_batch(session)
        assert len(claimed_b) == 1
        assert claimed_b[0]["id"] == ev_id
        assert claimed_b[0]["generation"] == 2
        assert claimed_b[0]["attempts"] == 2

        # Step 4: Stale Worker A tries to confirm with gen 1 -> MUST FAIL
        async with session_factory() as session:
            async with session.begin():
                fenced_out_w1 = await worker_a.confirm_published(session, ev_id, claimed_generation=1)
        assert fenced_out_w1 is False

        # Step 5: Stale Worker A tries to record failure with gen 1 -> MUST NOT change Worker B's lease
        async with session_factory() as session:
            async with session.begin():
                await worker_a.record_failure(session, ev_id, claimed_generation=1, error_message="stale error", attempts=1)

        # Verify database still has generation 2 and worker_id 'worker-B'
        async with session_factory() as session:
            ev = (await session.scalars(select(MhOutbox).where(MhOutbox.id == ev_id))).one()
            assert ev.lease_generation == 2
            assert ev.lease_worker_id == "worker-B"
            assert ev.last_error is None  # Worker A's record_failure was fenced out!

        # Step 6: Stale Worker A tries to confirm using gen 2 with Worker A's identity -> MUST FAIL
        async with session_factory() as session:
            async with session.begin():
                fenced_out_w1_spoof = await worker_a.confirm_published(session, ev_id, claimed_generation=2)
        assert fenced_out_w1_spoof is False

        # Step 7: Active Worker B confirms with gen 2 -> MUST SUCCEED
        async with session_factory() as session:
            async with session.begin():
                success_b = await worker_b.confirm_published(session, ev_id, claimed_generation=2)
        assert success_b is True

        # Step 8: Redundant confirmation on already published event -> MUST RETURN FALSE
        async with session_factory() as session:
            async with session.begin():
                redundant_b = await worker_b.confirm_published(session, ev_id, claimed_generation=2)
        assert redundant_b is False

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_poison_pill_max_attempts_isolation(disposable_pg_schema):
    """Verify that an event that reaches max_attempts is never claimed again (poison pill isolation)."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            async with session.begin():
                # Insert event with delivery_attempts == 10 (max_attempts)
                ev = MhOutbox(
                    id=uuid.uuid4(),
                    event_key="evt_poison_pill",
                    event_type="test.poison",
                    payload_json={"fatal": True},
                    delivery_attempts=10,
                    max_attempts=10,
                    lease_worker_id=None,
                    lease_generation=10,
                    lease_expires_at=None,
                    published_at=None,
                    created_at=utcnow(),
                )
                session.add(ev)

        worker = OutboxDispatcher(session_factory, worker_id="worker-test")

        # Attempt to claim
        async with session_factory() as session:
            async with session.begin():
                claimed = await worker.claim_batch(session)

        # MUST BE EMPTY (poison pill skipped)
        assert len(claimed) == 0

    finally:
        await engine.dispose()
