"""Programmatic migration runner for Mini-Hermes V2 persistence."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from open_webui.mini_hermes.v2.db import normalize_v2_db_url


def _get_alembic_v2_config(database_url: str | None = None) -> AlembicConfig:
    """Build AlembicConfig for alembic_v2.ini."""
    # Find alembic_v2.ini relative to backend root
    current = Path(__file__).resolve()
    # current: open_webui/mini_hermes/v2/migrations/runner.py
    # backend root is 5 levels up: runner -> migrations -> v2 -> mini_hermes -> open_webui -> backend
    backend_root = current.parents[4]
    ini_path = backend_root / "alembic_v2.ini"
    if not ini_path.exists():
        # Fallback to current working directory
        ini_path = Path("alembic_v2.ini").resolve()

    cfg = AlembicConfig(str(ini_path))
    migrations_dir = backend_root / "open_webui" / "migrations_v2"
    if not migrations_dir.exists():
        migrations_dir = Path("open_webui/migrations_v2").resolve()
    cfg.set_main_option("script_location", str(migrations_dir))

    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    # Escape % characters for configparser
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def run_v2_migrations(database_url: str | None = None, target_revision: str = "head") -> None:
    """Run V2 Alembic migrations to the specified revision."""
    cfg = _get_alembic_v2_config(database_url)
    if target_revision in ("base", "-1") or target_revision.startswith("-"):
        command.downgrade(cfg, target_revision)
    else:
        command.upgrade(cfg, target_revision)
        from open_webui.mini_hermes.v2.runtime_schema import ensure_runtime_tables_for_url

        ensure_runtime_tables_for_url(database_url)


def get_v2_current_revision(database_url: str | None = None) -> str | None:
    """Retrieve the currently applied revision ID from alembic_version table."""
    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            schema = conn.scalar(text("SELECT current_schema()")) or "public"
            result = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = 'alembic_version'"
                ),
                {"schema": schema},
            ).fetchone()
            if not result:
                return None
            tbl = f'"{schema}".alembic_version' if schema != "public" else "alembic_version"
            rev = conn.execute(text(f"SELECT version_num FROM {tbl} LIMIT 1")).scalar()
            return str(rev) if rev else None
    finally:
        engine.dispose()


def check_v2_migrations_up_to_date(database_url: str | None = None) -> bool:
    """Check if current database revision matches expected head."""
    current = get_v2_current_revision(database_url)
    head = ScriptDirectory.from_config(_get_alembic_v2_config(database_url)).get_current_head()
    return current is not None and current == head
