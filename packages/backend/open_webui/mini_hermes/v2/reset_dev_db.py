"""Dev Local Database Reset Script with 6 Defense-in-Depth Safety Guards.

Allows developers to reset their local database and re-bootstrap fresh V2 tables,
while strictly preventing execution against production, staging, or remote cloud databases.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.db import normalize_v2_db_url

BLOCKED_ENVS = frozenset({"production", "prod", "staging", "stage", "live"})
ALLOWED_DEV_ENVS = frozenset({"development", "dev", "local", "test"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "db", "0.0.0.0"})
DANGEROUS_HOST_SUBSTRINGS = ("prod", "stage", "rds.amazonaws.com", "azure.com", "cloudsql", "cockroachlabs.cloud")


class ProductionSafetyViolation(RuntimeError):
    """Raised when a dev reset safety guard is violated."""


def verify_safe_for_dev_reset(
    database_url: str | None = None,
    confirm: bool = False,
) -> None:
    """Evaluate all 6 defense-in-depth safety checks.

    Raises ProductionSafetyViolation if any check fails.
    """
    # Check 1: Explicit confirmation flag
    if not confirm:
        raise ProductionSafetyViolation(
            "Guard 1 failed: Dev reset requires explicit '--confirm' flag."
        )

    # Check 2: Mandatory MH_ALLOW_DEV_RESET=true environment variable
    allow_var = os.getenv("MH_ALLOW_DEV_RESET", "").strip().lower()
    if allow_var not in ("true", "1", "yes"):
        raise ProductionSafetyViolation(
            "Guard 2 failed: Dev reset requires environment variable 'MH_ALLOW_DEV_RESET=true'."
        )

    # Check 3: Reject production/staging environment flags
    for env_var in ("ENV", "ENVIRONMENT", "MINI_HERMES_ENV", "WEBUI_ENV", "NODE_ENV"):
        val = os.getenv(env_var, "").strip().lower()
        if val in BLOCKED_ENVS or any(b in val for b in BLOCKED_ENVS):
            raise ProductionSafetyViolation(
                f"Guard 3 failed: Environment variable {env_var}='{val}' indicates non-dev environment."
            )

    # Check 4: Whitelist allowed dev environments
    app_env = os.getenv("ENV") or os.getenv("ENVIRONMENT") or os.getenv("MINI_HERMES_ENV") or "development"
    app_env = app_env.strip().lower()
    if app_env not in ALLOWED_DEV_ENVS:
        raise ProductionSafetyViolation(
            f"Guard 4 failed: Environment '{app_env}' is not in allowed dev environments {sorted(ALLOWED_DEV_ENVS)}."
        )

    # Parse database URL
    url = database_url or os.getenv("DATABASE_URL", "")
    if not url:
        raise ProductionSafetyViolation("Guard 5 failed: DATABASE_URL is not set.")

    # Remove driver prefix for urlparse if needed
    clean_url = url.split("://")[-1] if "://" in url else url
    # Add dummy scheme for urlparse
    parsed = urlparse(f"dummy://{clean_url}")
    hostname = (parsed.hostname or "").lower()

    # Check 5: Host must be local
    if hostname not in LOCAL_HOSTS:
        raise ProductionSafetyViolation(
            f"Guard 5 failed: Host '{hostname}' is not a local development host {sorted(LOCAL_HOSTS)}."
        )
    if any(danger in hostname for danger in DANGEROUS_HOST_SUBSTRINGS):
        raise ProductionSafetyViolation(
            f"Guard 5 failed: Host '{hostname}' contains dangerous remote substring."
        )

    # Check 6: Database name must not contain prod/stage/live (unquote percent-encoding)
    raw_path = parsed.path.lstrip("/") or ""
    current_dbname = raw_path
    for _ in range(5):  # Bounded iterative unquoting defeats single and nested encoding
        decoded = unquote(current_dbname)
        if decoded == current_dbname:
            break
        current_dbname = decoded
    dbname = current_dbname.lower()

    if any(b in dbname for b in ("prod", "production", "stage", "staging", "live")):
        raise ProductionSafetyViolation(
            f"Guard 6 failed: Database name '{dbname}' contains protected keyword."
        )


async def reset_dev_database(
    database_url: str | None = None,
    confirm: bool = False,
) -> dict:
    """Execute dev database reset after all safety checks pass."""
    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    verify_safe_for_dev_reset(url, confirm=confirm)

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Drop all objects in public schema
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))

        # Re-bootstrap fresh V2 database
        summary = await bootstrap_v2_database(
            BootstrapConfig(database_url=url, environment_id="development"),
            engine=engine,
        )
        return summary
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset local Mini-Hermes V2 development database.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm you genuinely want to wipe the local dev database.",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Database URL (defaults to DATABASE_URL env var).",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(reset_dev_database(database_url=args.database_url, confirm=args.confirm))
        print("Dev database reset and bootstrapped successfully!")
        print(f"Instance ID: {result['instance_id']}")
        print(f"Admin email: {result['admin_email']}")
    except ProductionSafetyViolation as psv:
        sys.stderr.write(f"\n[SAFETY BLOCKED] {psv}\n")
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write(f"\n[ERROR] Reset failed: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
