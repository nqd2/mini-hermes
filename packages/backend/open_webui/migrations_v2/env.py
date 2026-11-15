"""Alembic environment runner for Mini-Hermes V2 greenfield persistence.

Binds exclusively to V2Base.metadata (49 mh_* tables) and refuses legacy V1 databases.
"""

from __future__ import annotations

import logging
import logging.config
import os

import alembic.context
from sqlalchemy import create_engine, inspect, pool, text

from open_webui.mini_hermes.v2.models import v2_metadata

# Alembic Config object
alembic_config = alembic.context.config

if alembic_config.config_file_name:
    logging.config.fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = v2_metadata


def get_target_db_url() -> str:
    """Resolve and normalize database URL for synchronous Alembic operations."""
    url = (
        alembic_config.get_main_option("sqlalchemy.url")
        or os.getenv("DATABASE_URL")
        or ""
    )
    if not url:
        raise ValueError("DATABASE_URL is not set for V2 migrations")

    if url.startswith(("sqlite://", "sqlite+")):
        raise RuntimeError("Mini-Hermes V2 strictly forbids SQLite. PostgreSQL required.")

    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)

    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL DDL script)."""
    url = get_target_db_url()
    alembic.context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
    )

    with alembic.context.begin_transaction():
        alembic.context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live PostgreSQL database."""
    target_url = get_target_db_url()
    connectable = create_engine(
        target_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Pre-migration fail-closed guard: check for legacy V1 tables
        inspector = inspect(connection)
        existing_tables = inspector.get_table_names()
        legacy_tables = [
            t for t in existing_tables
            if not t.startswith("mh_") and t not in ("alembic_version", "alembic_version_v2")
        ]
        if legacy_tables:
            raise RuntimeError(
                f"FATAL: Legacy V1 tables detected in target database: {legacy_tables}. "
                "Mini-Hermes V2 refuses to migrate a legacy database to protect V1 production data."
            )

        current_schema = connection.scalar(text("SELECT current_schema()"))
        config_kwargs: dict = {
            "connection": connection,
            "target_metadata": target_metadata,
            "version_table": "alembic_version",
        }
        if current_schema and current_schema != "public":
            config_kwargs["version_table_schema"] = current_schema

        alembic.context.configure(**config_kwargs)

        with alembic.context.begin_transaction():
            alembic.context.run_migrations()
        connection.commit()


if alembic.context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
