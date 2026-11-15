"""V2 Database engine, session factory, dialect enforcement, and authorization advisory lock."""

import os
import sys
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.models.base import enforce_postgresql

_DOCUMENT_AUTHORIZATION_LOCK = 724603816392475001


def normalize_v2_db_url(url: str | None = None) -> str:
    """Normalize database URL to async PostgreSQL (psycopg v3).

    Strictly refuses SQLite or non-PostgreSQL dialects.
    """
    db_url = url or os.getenv("DATABASE_URL", "")
    if not db_url:
        raise ValueError("DATABASE_URL is not set")

    if db_url.startswith(("sqlite://", "sqlite+")):
        raise RuntimeError(
            f"Mini-Hermes V2 strictly forbids SQLite ('{db_url}'). "
            "Configure a dedicated PostgreSQL database."
        )

    if db_url.startswith("postgresql+psycopg://"):
        return db_url
    if db_url.startswith("postgresql+psycopg2://"):
        return db_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if db_url.startswith("postgresql+asyncpg://"):
        return db_url
    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql+psycopg://", 1)

    # Dialect check
    if not (db_url.startswith("postgresql") or db_url.startswith("postgres")):
        raise RuntimeError(
            f"Unsupported database URL dialect: '{db_url}'. Mini-Hermes V2 requires PostgreSQL."
        )

    return db_url


def create_v2_async_engine(database_url: str | None = None, **kwargs) -> AsyncEngine:
    """Create a new AsyncEngine for Mini-Hermes V2 persistence."""
    normalized_url = normalize_v2_db_url(database_url)

    if sys.platform == "win32" and "psycopg" in normalized_url:
        try:
            from open_webui.utils.windows_asyncio import install_windows_selector_policy

            install_windows_selector_policy()
        except ImportError:
            pass

    engine_kwargs = {
        "pool_pre_ping": True,
        **kwargs,
    }
    return create_async_engine(normalized_url, **engine_kwargs)


def create_v2_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an AsyncSession factory bound to the provided engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


async def lock_v2_authorization_gate(db: AsyncSession, *, exclusive: bool) -> None:
    """Acquire the PostgreSQL transaction-level advisory lock for document authorization.

    Ensures zero race conditions between permission changes and concurrent revision commits.
    Requires PostgreSQL and READ COMMITTED isolation.
    """
    enforce_postgresql(db.bind.dialect.name if db.bind else "unknown")

    isolation = await db.scalar(text("SELECT current_setting('transaction_isolation')"))
    if isolation != "read committed":
        raise RuntimeError(
            f"Document authorization requires PostgreSQL READ COMMITTED (found '{isolation}')"
        )

    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await db.execute(text(f"SELECT {function}(:key)"), {"key": _DOCUMENT_AUTHORIZATION_LOCK})


async def get_v2_session_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Async generator yielding an AsyncSession with auto-cleanup."""
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
