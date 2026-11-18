"""Centralized test configuration and fixtures for Agent Runtime V2 4-Tier E2E Test Suite.

Provides:
- Windows event loop policy configuration
- In-memory async SQLite engine and session fixtures
- Standard in-memory ObjectStore fixture
- Test user profiles (admin, standard, revoked)
- FastAPI test application with dependency overrides
- AsyncClient (httpx.AsyncClient with ASGITransport)
- Format fixture path resolvers and byte loaders
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Ensure default test environment configuration before any application imports
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("WEAI_SECRET_KEY", "test-secret-key-32-chars-long!!")
os.environ.setdefault("AGENT_RUNTIME_TOKEN_ENCRYPTION_KEY", "test-encryption-key-32-chars!!")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("AGENT_RUNTIME_SKIP_STARTUP_VALIDATION", "true")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

from weai.agent_runtime.models import AgentEvent, AgentSession, AgentToolInvocation, AgentTurn
from weai.agent_runtime.repository import AgentRepository
from weai.agent_runtime.models import Base as AgentBase
from weai.internal.db import Base
from weai.documents import revision_service, routes
from weai.documents.models import (
    Document,
    DocumentChunk,
    Folder,
    Preview,
    ResourceIndexJob,
    ResourceTask,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
)
from weai.documents.revision_integrity import compute_patch_hash
import weai.models.access_grants
import weai.models.chats
import weai.models.files
import weai.models.groups
import weai.models.users

# Resolve fixtures directory
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "v2"


@pytest.fixture(scope="session")
def event_loop_policy():
    """Ensure WindowsSelectorEventLoopPolicy on Windows for async I/O compatibility."""
    if sys.platform == "win32":
        policy = asyncio.WindowsSelectorEventLoopPolicy()
        asyncio.set_event_loop_policy(policy)
        return policy
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Return the absolute path to tests/fixtures/v2."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def fixture_manifest(fixtures_dir: Path) -> dict[str, str]:
    """Load SHA-256 digests from SHA256SUMS.txt."""
    manifest_path = fixtures_dir / "SHA256SUMS.txt"
    if not manifest_path.exists():
        return {}
    manifest = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            manifest[parts[1].strip()] = parts[0].strip()
    return manifest


class MemoryObjectStore:
    """In-memory object storage simulating S3/SeaweedFS for immutable test bytes."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(objects or {})

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(f"Object {key} not found")
        return self.objects[key]

    async def put(self, key: str, content: bytes, mime_type: str = "application/octet-stream") -> None:
        self.objects[key] = bytes(content)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects


@pytest_asyncio.fixture
async def memory_store() -> MemoryObjectStore:
    """Yield a fresh in-memory object store."""
    return MemoryObjectStore()


@pytest_asyncio.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an isolated, in-memory SQLite async engine with all V2, RBAC, and AgentRuntime tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AgentBase.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Yield a sessionmaker bound to the isolated in-memory test database."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def bind_global_session(db_sessionmaker, monkeypatch):
    monkeypatch.setattr("weai.internal.db.AsyncSessionLocal", db_sessionmaker)


@pytest_asyncio.fixture
async def db_session(db_sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    """Yield an open, isolated database session."""
    async with db_sessionmaker() as session:
        yield session


@pytest.fixture
def mock_standard_user():
    return SimpleNamespace(id="user-std-1", role="user", name="Standard Test User", email="std@example.com")


@pytest.fixture
def mock_admin_user():
    return SimpleNamespace(id="user-adm-1", role="admin", name="Admin Test User", email="admin@example.com")


@pytest.fixture
def mock_revoked_user():
    return SimpleNamespace(id="user-rev-1", role="user", name="Revoked Test User", email="revoked@example.com")


@pytest.fixture
def app(db_sessionmaker: async_sessionmaker[AsyncSession], memory_store: MemoryObjectStore, mock_standard_user, monkeypatch):
    """Build a FastAPI test app with Agent Runtime router mounted and dependencies overridden."""
    test_app = FastAPI()
    test_app.include_router(routes.router, prefix="/api/v1/agent-runtime")

    # Dependency overrides
    test_app.dependency_overrides[routes.get_verified_user] = lambda: mock_standard_user

    async def override_get_async_session():
        async with db_sessionmaker() as session:
            yield session

    test_app.dependency_overrides[routes.get_async_session] = override_get_async_session

    # Monkeypatch ObjectStore across routes and revision service
    monkeypatch.setattr(routes, "ObjectStore", lambda: memory_store)
    monkeypatch.setattr(revision_service, "ObjectStore", lambda: memory_store)

    return test_app


@pytest_asyncio.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an httpx AsyncClient talking directly to the FastAPI ASGI test app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
