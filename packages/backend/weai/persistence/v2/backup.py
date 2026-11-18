"""Dual-Mode Backup & Restore Utility for Agent Runtime V2 Greenfield Persistence.

Supports:
1. Native pg_dump / psql CLI when available in the system PATH.
2. Pure Python / SQLAlchemy async fallback for environments without PostgreSQL CLI binaries.
3. Strict safety guards preventing restores onto production targets without force=True.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Date, DateTime, LargeBinary, MetaData, Uuid, and_, func, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from weai.persistence.v2.db import normalize_v2_db_url
from weai.persistence.v2.models import utcnow

log = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (bytes, memoryview)):
        return obj.hex()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def _restore_row(table, row: dict[str, Any]) -> dict[str, Any]:
    """Recover typed values from the JSON backup representation."""
    converted = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if isinstance(value, str):
            if isinstance(column.type, Uuid):
                value = UUID(value)
            elif isinstance(column.type, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(column.type, Date):
                value = date.fromisoformat(value)
            elif isinstance(column.type, LargeBinary):
                value = bytes.fromhex(value)
        converted[column.name] = value
    return converted


async def _verify_restored_foreign_keys(conn, metadata: MetaData) -> None:
    """Check every FK after loading rows with triggers temporarily disabled."""
    for table in metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            referred = elements[0].column.table.alias()
            matches = and_(*(
                element.parent == referred.c[element.column.name]
                for element in elements
            ))
            missing = and_(
                *(element.parent.is_not(None) for element in elements),
                referred.c[elements[0].column.name].is_(None),
            )
            orphan_count = await conn.scalar(
                select(func.count()).select_from(table.outerjoin(referred, matches)).where(missing)
            )
            if orphan_count:
                raise RuntimeError(
                    f"Restored table {table.name} violates foreign key {constraint.name}"
                )


async def dump_v2_database(
    source_url: str | None = None,
    output_path: str | Path = "backup_v2.dump",
) -> dict[str, Any]:
    """Dump every release table and environment identity.

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
            "format": "agent-runtime-v2-json-dump",
            "created_at": utcnow().isoformat(),
            "tables": {},
            "row_counts": {},
        }

        async with engine.connect() as conn:
            def _reflect(sync_conn):
                metadata = MetaData()
                names = [
                    name for name in inspect(sync_conn).get_table_names()
                    if name.startswith("ar_")
                ]
                metadata.reflect(bind=sync_conn, only=names)
                return metadata

            metadata = await conn.run_sync(_reflect)
            for tbl_name in sorted(metadata.tables):
                rows = (await conn.execute(select(metadata.tables[tbl_name]))).mappings().all()
                serialized_rows = [dict(row) for row in rows]
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
    if data.get("format") != "agent-runtime-v2-json-dump" or not isinstance(data.get("tables"), dict):
        raise ValueError("Unsupported or incomplete Agent Runtime backup format")

    from weai.persistence.v2.migrations.runner import run_v2_migrations

    await asyncio.to_thread(run_v2_migrations, url)
    engine = create_async_engine(url)

    try:
        async with engine.begin() as conn:
            def _reflect(sync_conn):
                metadata = MetaData()
                names = [
                    name for name in inspect(sync_conn).get_table_names()
                    if name.startswith("ar_")
                ]
                metadata.reflect(bind=sync_conn, only=names)
                return metadata

            metadata = await conn.run_sync(_reflect)
            if set(data["tables"]) != set(metadata.tables):
                raise ValueError("Backup tables do not match the target release schema")
            if any(
                data.get("row_counts", {}).get(name) != len(rows)
                for name, rows in data["tables"].items()
            ):
                raise ValueError("Backup row-count manifest does not match its contents")

            # Trigger suppression is scoped to this transaction. Verify every FK
            # explicitly before committing the restored data.
            await conn.execute(text("SET session_replication_role = 'replica';"))

            tables_restored = 0
            rows_restored = 0

            quoted_names = ", ".join(f'"{name}"' for name in sorted(metadata.tables))
            await conn.exec_driver_sql(f"TRUNCATE TABLE {quoted_names} CASCADE")

            for table in metadata.sorted_tables:
                rows = data["tables"][table.name]
                if rows:
                    await conn.execute(table.insert(), [_restore_row(table, row) for row in rows])
                    rows_restored += len(rows)
                tables_restored += 1

            await conn.execute(text("SET session_replication_role = 'origin';"))
            await _verify_restored_foreign_keys(conn, metadata)

        return {
            "mode": "python_async",
            "tables_restored": tables_restored,
            "rows_restored": rows_restored,
            "status": "restored",
        }
    finally:
        await engine.dispose()
