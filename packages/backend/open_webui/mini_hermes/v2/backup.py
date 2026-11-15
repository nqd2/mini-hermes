"""Dual-Mode Backup & Restore Utility for Mini-Hermes V2 Greenfield Persistence.

Supports:
1. Native pg_dump / psql CLI when available in the system PATH.
2. Pure Python / SQLAlchemy async fallback for environments without PostgreSQL CLI binaries.
3. Strict safety guards preventing restores onto production targets without force=True.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from open_webui.mini_hermes.v2.db import normalize_v2_db_url
from open_webui.mini_hermes.v2.models import utcnow, v2_metadata

log = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    raise TypeError(f"Type {type(obj)} not serializable")


async def dump_v2_database(
    source_url: str | None = None,
    output_path: str | Path = "backup_v2.dump",
) -> dict[str, Any]:
    """Dump all 49 mh_* tables and environment identity.

    Tries pg_dump first; falls back to pure Python async dumper if pg_dump is unavailable.
    """
    url = normalize_v2_db_url(source_url or os.getenv("DATABASE_URL"))
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Check if native pg_dump is available
    pg_dump_bin = shutil.which("pg_dump")
    if pg_dump_bin:
        try:
            # Strip dialect for standard pg_dump
            clean_url = url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
            cmd = [
                pg_dump_bin,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                f"--file={str(out_file)}",
                clean_url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            log.info("Native pg_dump completed: %s", result.stdout)
            return {"mode": "pg_dump", "output_path": str(out_file), "success": True}
        except Exception as exc:
            log.warning("pg_dump invocation failed (%s), falling back to Python dumper.", exc)

    # Pure Python async dumper fallback
    engine = create_async_engine(url)
    try:
        dump_payload: dict[str, Any] = {
            "version": "2.0",
            "format": "mini-hermes-v2-json-dump",
            "created_at": utcnow().isoformat(),
            "tables": {},
            "row_counts": {},
        }

        async with engine.connect() as conn:
            def _get_tables(sync_conn):
                inspector = inspect(sync_conn)
                return [t for t in inspector.get_table_names() if t.startswith("mh_")]

            mh_tables = await conn.run_sync(_get_tables)

            for tbl_name in sorted(mh_tables):
                if tbl_name in v2_metadata.tables:
                    table_obj = v2_metadata.tables[tbl_name]
                    stmt = select(table_obj)
                    rows = (await conn.execute(stmt)).mappings().all()

                    serialized_rows = [dict(r) for r in rows]
                    dump_payload["tables"][tbl_name] = serialized_rows
                    dump_payload["row_counts"][tbl_name] = len(serialized_rows)

        # Write serialized dump to file
        dump_json = json.dumps(dump_payload, default=_json_serial, indent=2)
        out_file.write_text(dump_json, encoding="utf-8")

        return {
            "mode": "python_async",
            "output_path": str(out_file),
            "tables_dumped": len(dump_payload["tables"]),
            "row_counts": dump_payload["row_counts"],
            "success": True,
        }
    finally:
        await engine.dispose()


async def restore_v2_database(
    target_url: str | None = None,
    dump_path: str | Path = "backup_v2.dump",
    force: bool = False,
) -> dict[str, Any]:
    """Restore a dumped V2 database into the target PostgreSQL database."""
    url = normalize_v2_db_url(target_url or os.getenv("DATABASE_URL"))
    dump_file = Path(dump_path)

    if not dump_file.exists():
        raise FileNotFoundError(f"Dump file '{dump_path}' not found")

    # Safety check: avoid accidentally overwriting non-dev without force
    env_name = os.getenv("ENV", "development").lower()
    if env_name in ("production", "prod", "staging") and not force:
        raise RuntimeError(
            f"Refusing to restore onto '{env_name}' database without explicit force=True."
        )

    # Check if dump is JSON fallback or SQL script
    content_peek = dump_file.read_bytes()[:200]
    is_json = content_peek.strip().startswith(b"{")

    if not is_json:
        # Native psql CLI restore
        psql_bin = shutil.which("psql")
        if not psql_bin:
            raise RuntimeError("psql CLI required to restore raw SQL dump but not found in PATH.")
        clean_url = url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
        cmd = [psql_bin, "-d", clean_url, "-f", str(dump_file)]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {"mode": "psql", "status": "restored"}

    # Python async restore from JSON dump
    data = json.loads(dump_file.read_text(encoding="utf-8"))
    engine = create_async_engine(url)

    try:
        async with engine.begin() as conn:
            # 1. Create tables if not present
            def _create_all(sync_conn):
                v2_metadata.create_all(bind=sync_conn)

            await conn.run_sync(_create_all)

            # 2. Disable triggers/FK checking during restore transaction
            await conn.execute(text("SET session_replication_role = 'replica';"))

            tables_restored = 0
            rows_restored = 0

            # 3. Clear existing table data to ensure clean restore
            for tbl_name in reversed(list(v2_metadata.tables.keys())):
                if tbl_name in data["tables"]:
                    await conn.execute(text(f'TRUNCATE TABLE "{tbl_name}" CASCADE;'))

            # 4. Insert table rows
            for tbl_name, rows in data["tables"].items():
                if tbl_name in v2_metadata.tables and rows:
                    table_obj = v2_metadata.tables[tbl_name]
                    await conn.execute(table_obj.insert(), rows)
                    rows_restored += len(rows)
                tables_restored += 1

            # 5. Re-enable triggers and foreign keys
            await conn.execute(text("SET session_replication_role = 'origin';"))

        return {
            "mode": "python_async",
            "tables_restored": tables_restored,
            "rows_restored": rows_restored,
            "status": "restored",
        }
    finally:
        await engine.dispose()
