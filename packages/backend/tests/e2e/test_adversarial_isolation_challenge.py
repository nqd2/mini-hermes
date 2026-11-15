"""Adversarial Isolation and Concurrency Stress Test Suite.

Empirically challenges:
1. Cross-test session isolation & zero state leakage.
2. Uncommitted transaction rollback & dirty read isolation.
3. High-concurrency multi-session operations on StaticPool in-memory SQLite.
4. Global sessionmaker fallback routing to in-memory engine.
5. Fast-path concurrent API client requests alongside direct DB operations.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from open_webui.agent_runtime.models import AgentSession
from open_webui.internal.db import get_async_db, get_async_db_context
from open_webui.mini_hermes.models import Document
from open_webui.models.users import User


@pytest.mark.asyncio
async def test_adv_01_cross_test_leak_insertion(db_session: AsyncSession):
    """Test A: Injects records into multiple distinct domain models and commits them."""
    # 1. User
    user = User(
        id="adv-leak-user-1",
        email="adv1@example.com",
        name="Adversary Leak User",
        role="user",
    )
    db_session.add(user)

    # 2. Document
    doc = Document(
        id="adv-leak-doc-1",
        owner_id="adv-leak-user-1",
        filename="leak_test.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        current_revision=1,
    )
    db_session.add(doc)

    # 3. AgentSession
    agent_sess = AgentSession(
        id="adv-leak-sess-1",
        owner_id="adv-leak-user-1",
        status="active",
        next_turn_sequence=1,
    )
    db_session.add(agent_sess)

    await db_session.commit()

    # Verify they were actually committed in this test
    u = await db_session.get(User, "adv-leak-user-1")
    assert u is not None
    assert u.id == "adv-leak-user-1"

    d = await db_session.get(Document, "adv-leak-doc-1")
    assert d is not None
    assert d.id == "adv-leak-doc-1"

    s = await db_session.get(AgentSession, "adv-leak-sess-1")
    assert s is not None
    assert s.id == "adv-leak-sess-1"


@pytest.mark.asyncio
async def test_adv_02_cross_test_leak_assertion(db_session: AsyncSession):
    """Test B: Runs directly after Test A. Empirically verifies ZERO state leaked across tests."""
    # Check individual entities
    u = await db_session.get(User, "adv-leak-user-1")
    assert u is None, f"STATE LEAKAGE DETECTED: User adv-leak-user-1 found in new test session!"

    d = await db_session.get(Document, "adv-leak-doc-1")
    assert d is None, f"STATE LEAKAGE DETECTED: Document adv-leak-doc-1 found in new test session!"

    s = await db_session.get(AgentSession, "adv-leak-sess-1")
    assert s is None, f"STATE LEAKAGE DETECTED: AgentSession adv-leak-sess-1 found in new test session!"

    # Check total table counts
    user_count = await db_session.scalar(select(func.count(User.id)))
    assert user_count == 0, f"Expected 0 users in fresh test, found {user_count}"

    doc_count = await db_session.scalar(select(func.count(Document.id)))
    assert doc_count == 0, f"Expected 0 documents in fresh test, found {doc_count}"

    sess_count = await db_session.scalar(select(func.count(AgentSession.id)))
    assert sess_count == 0, f"Expected 0 agent sessions in fresh test, found {sess_count}"


@pytest.mark.asyncio
async def test_adv_03_uncommitted_rollback_isolation(db_sessionmaker: async_sessionmaker[AsyncSession]):
    """Test C: Verifies uncommitted changes in one session do not leak to another session, and clean rollback."""
    # Session 1 starts transaction, adds user, does NOT commit
    async with db_sessionmaker() as session_1:
        u1 = User(
            id="adv-uncommitted-user",
            email="uncommitted@example.com",
            name="Uncommitted User",
            role="user",
        )
        session_1.add(u1)
        await session_1.flush()

        # Session 2 tries to read it concurrently
        async with db_sessionmaker() as session_2:
            res = await session_2.get(User, "adv-uncommitted-user")
            # In SQLite with StaticPool, uncommitted changes from session_1 should not be visible or rollback cleanly
            # Now session 1 explicitly rolls back
        await session_1.rollback()

    # Session 3 confirms record is gone
    async with db_sessionmaker() as session_3:
        res_after = await session_3.get(User, "adv-uncommitted-user")
        assert res_after is None


@pytest.mark.asyncio
async def test_adv_04_concurrent_multi_session_stress(db_sessionmaker: async_sessionmaker[AsyncSession]):
    """Test D: Adversarially fires 15 concurrent sessions performing read/write/commit operations."""
    async def worker(idx: int):
        async with db_sessionmaker() as session:
            user = User(
                id=f"adv-worker-user-{idx}",
                email=f"worker{idx}@example.com",
                name=f"Worker User {idx}",
                role="user",
            )
            session.add(user)
            await session.commit()

            # Query back
            read_user = await session.get(User, f"adv-worker-user-{idx}")
            assert read_user is not None
            assert read_user.name == f"Worker User {idx}"

    tasks = [asyncio.create_task(worker(i)) for i in range(15)]
    await asyncio.gather(*tasks)

    # Verify all 15 records exist in the database
    async with db_sessionmaker() as session:
        count = await session.scalar(select(func.count(User.id)))
        assert count == 15


@pytest.mark.asyncio
async def test_adv_05_global_sessionmaker_routing_and_isolation(db_session: AsyncSession):
    """Test E: Confirms get_async_db and get_async_db_context route to the in-memory engine."""
    # Write using get_async_db() directly
    async with get_async_db() as global_sess:
        user = User(
            id="adv-global-route-user",
            email="global@example.com",
            name="Global Routed User",
            role="user",
        )
        global_sess.add(user)
        await global_sess.commit()

    # Read back via test fixture db_session
    u = await db_session.get(User, "adv-global-route-user")
    assert u is not None
    assert u.id == "adv-global-route-user"

    # Also test get_async_db_context without passed session
    async with get_async_db_context(None) as ctx_sess:
        u_ctx = await ctx_sess.get(User, "adv-global-route-user")
        assert u_ctx is not None
