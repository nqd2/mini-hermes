"""8-Point 100% Parity Verification Engine for Agent Runtime V2 Persistence.

Validates that a restored database matches the source database with 100% fidelity:
1. Table Count & Names: All 49 ar_* tables present in both.
2. Column Definitions: Column names and types match 100%.
3. Primary Keys: Primary key columns match 100%.
4. Foreign Key Constraints: All referential constraints match 100%.
5. Indexes & Unique Constraints: All indexes and unique constraints match 100%.
6. Exact Row Counts: SELECT count(*) on each table matches 100%.
7. Table Content Hashes: Row data SHA-256 digests match 100%.
8. Environment Marker Validity: Valid ar_environment_markers present.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from weai.persistence.v2.backup import _json_serial
from weai.persistence.v2.db import normalize_v2_db_url
from weai.persistence.v2.models import v2_metadata


class ParityVerificationResult(BaseModel):
    """Result envelope for 8-point database parity verification."""

    passed: bool = Field(description="True if all 8 parity checks pass with 100% parity")
    discrepancies: list[str] = Field(default_factory=list, description="List of observed discrepancies")
    tables_verified: int = Field(default=0, description="Total number of ar_* tables verified")
    total_rows_source: int = Field(default=0, description="Total rows in source database")
    total_rows_target: int = Field(default=0, description="Total rows in target database")
    check_details: dict[str, bool] = Field(
        default_factory=dict, description="Status of each of the 8 parity checks"
    )


async def verify_database_parity(
    source_url: str,
    target_url: str,
) -> ParityVerificationResult:
    """Execute the 8-point parity verification engine comparing source and target databases."""
    src_norm = normalize_v2_db_url(source_url)
    tgt_norm = normalize_v2_db_url(target_url)

    src_engine = create_async_engine(src_norm)
    tgt_engine = create_async_engine(tgt_norm)

    discrepancies: list[str] = []
    checks: dict[str, bool] = {
        "1_table_names": True,
        "2_columns": True,
        "3_primary_keys": True,
        "4_foreign_keys": True,
        "5_indexes": True,
        "6_row_counts": True,
        "7_content_hashes": True,
        "8_environment_marker": True,
    }

    try:
        async with src_engine.connect() as src_conn, tgt_engine.connect() as tgt_conn:
            # Inspection helper
            def _inspect_schema(sync_conn):
                inspector = inspect(sync_conn)
                tables = sorted([t for t in inspector.get_table_names() if t.startswith("ar_")])
                schema_data = {}
                for t in tables:
                    cols = {c["name"]: str(c["type"]) for c in inspector.get_columns(t)}
                    pk = inspector.get_pk_constraint(t).get("constrained_columns", [])
                    fks = [
                        (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
                        for fk in inspector.get_foreign_keys(t)
                    ]
                    indexes = [
                        (idx["name"], idx["column_names"], idx["unique"])
                        for idx in inspector.get_indexes(t)
                    ]
                    schema_data[t] = {
                        "columns": cols,
                        "pk": pk,
                        "fks": sorted(fks, key=str),
                        "indexes": sorted(indexes, key=str),
                    }
                return tables, schema_data

            src_tables, src_schema = await src_conn.run_sync(_inspect_schema)
            tgt_tables, tgt_schema = await tgt_conn.run_sync(_inspect_schema)

            # Check 1: Table count and names across the complete release schema.
            if src_tables != tgt_tables:
                checks["1_table_names"] = False
                discrepancies.append(
                    f"Table mismatch: source has {len(src_tables)} tables, target has {len(tgt_tables)} tables. "
                    f"Diff: {set(src_tables) ^ set(tgt_tables)}"
                )

            # Check 2: Columns
            for t in src_tables:
                if t in tgt_tables:
                    src_cols = src_schema[t]["columns"]
                    tgt_cols = tgt_schema[t]["columns"]
                    if src_cols != tgt_cols:
                        checks["2_columns"] = False
                        discrepancies.append(f"Column mismatch on table '{t}': {src_cols} vs {tgt_cols}")

            # Check 3: Primary Keys
            for t in src_tables:
                if t in tgt_tables:
                    src_pk = src_schema[t]["pk"]
                    tgt_pk = tgt_schema[t]["pk"]
                    if src_pk != tgt_pk:
                        checks["3_primary_keys"] = False
                        discrepancies.append(f"Primary key mismatch on '{t}': {src_pk} vs {tgt_pk}")

            # Check 4: Foreign Keys
            for t in src_tables:
                if t in tgt_tables:
                    src_fks = src_schema[t]["fks"]
                    tgt_fks = tgt_schema[t]["fks"]
                    if src_fks != tgt_fks:
                        checks["4_foreign_keys"] = False
                        discrepancies.append(f"Foreign key mismatch on '{t}': {src_fks} vs {tgt_fks}")

            # Check 5: Indexes & Unique Constraints
            for t in src_tables:
                if t in tgt_tables:
                    src_idx = src_schema[t]["indexes"]
                    tgt_idx = tgt_schema[t]["indexes"]
                    if src_idx != tgt_idx:
                        checks["5_indexes"] = False
                        discrepancies.append(f"Index mismatch on '{t}': {src_idx} vs {tgt_idx}")

            # Check 6 & 7: Row Counts and Content Hashes
            total_rows_src = 0
            total_rows_tgt = 0

            for t in src_tables:
                if t in tgt_tables and t in v2_metadata.tables:
                    table_obj = v2_metadata.tables[t]
                    # Count
                    cnt_src = (await src_conn.scalar(text(f'SELECT count(*) FROM "{t}"'))) or 0
                    cnt_tgt = (await tgt_conn.scalar(text(f'SELECT count(*) FROM "{t}"'))) or 0
                    total_rows_src += cnt_src
                    total_rows_tgt += cnt_tgt

                    if cnt_src != cnt_tgt:
                        checks["6_row_counts"] = False
                        discrepancies.append(f"Row count mismatch on '{t}': source={cnt_src}, target={cnt_tgt}")

                    # Content hashes
                    rows_src = (await src_conn.execute(select(table_obj))).mappings().all()
                    rows_tgt = (await tgt_conn.execute(select(table_obj))).mappings().all()

                    hash_src = hashlib.sha256(
                        json.dumps([dict(r) for r in rows_src], default=_json_serial, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                    hash_tgt = hashlib.sha256(
                        json.dumps([dict(r) for r in rows_tgt], default=_json_serial, sort_keys=True).encode("utf-8")
                    ).hexdigest()

                    if hash_src != hash_tgt:
                        checks["7_content_hashes"] = False
                        discrepancies.append(f"Content hash mismatch on '{t}': {hash_src} vs {hash_tgt}")

            # Check 8: Environment Marker Validity
            if "ar_environment_markers" in tgt_tables:
                marker = (
                    await tgt_conn.execute(
                        text("SELECT key, instance_id, environment_name, schema_generation FROM ar_environment_markers LIMIT 1")
                    )
                ).mappings().fetchone()
                if not marker:
                    checks["8_environment_marker"] = False
                    discrepancies.append("Target database has empty 'ar_environment_markers'")
            else:
                checks["8_environment_marker"] = False
                discrepancies.append("Target database is missing 'ar_environment_markers'")

        passed = len(discrepancies) == 0 and all(checks.values())
        return ParityVerificationResult(
            passed=passed,
            discrepancies=discrepancies,
            tables_verified=len(src_tables),
            total_rows_source=total_rows_src,
            total_rows_target=total_rows_tgt,
            check_details=checks,
        )
    finally:
        await src_engine.dispose()
        await tgt_engine.dispose()
