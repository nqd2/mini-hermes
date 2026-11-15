"""Tests for Dev DB Reset Safety Guards and Execution (M1.4)."""

import os
import pytest

from open_webui.mini_hermes.v2.reset_dev_db import (
    ProductionSafetyViolation,
    verify_safe_for_dev_reset,
)


def test_guard_1_fails_without_confirm():
    """Dev reset must be rejected if confirm=False."""
    with pytest.raises(ProductionSafetyViolation, match="Guard 1 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes", confirm=False)


def test_guard_2_fails_without_allow_env_var(monkeypatch):
    """Dev reset must be rejected if MH_ALLOW_DEV_RESET!=true."""
    monkeypatch.delenv("MH_ALLOW_DEV_RESET", raising=False)
    with pytest.raises(ProductionSafetyViolation, match="Guard 2 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes", confirm=True)


def test_guard_3_fails_on_production_environment_variables(monkeypatch):
    """Dev reset must be rejected if ENV/ENVIRONMENT contains production or staging."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")

    for bad_env in ("production", "prod", "staging", "stage", "live"):
        monkeypatch.setenv("ENV", bad_env)
        with pytest.raises(ProductionSafetyViolation, match="Guard 3 failed"):
            verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes", confirm=True)


def test_guard_4_fails_on_unwhitelisted_environment(monkeypatch):
    """Dev reset must be rejected if environment is not in allowed whitelist."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "unknown_custom_cluster")

    with pytest.raises(ProductionSafetyViolation, match="Guard 4 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes", confirm=True)


def test_guard_5_fails_on_remote_or_cloud_database_hosts(monkeypatch):
    """Dev reset must reject cloud hosts (RDS, Azure, GCP, etc.) and non-local hosts."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    dangerous_urls = [
        "postgresql://user:pass@prod-postgres.internal:5432/hermes_dev",
        "postgresql://user:pass@mydb.rds.amazonaws.com:5432/hermes_dev",
        "postgresql://user:pass@hermes.postgres.database.azure.com:5432/hermes",
        "postgresql://user:pass@192.168.1.100:5432/hermes_dev",
    ]

    for bad_url in dangerous_urls:
        with pytest.raises(ProductionSafetyViolation, match="Guard 5 failed"):
            verify_safe_for_dev_reset(bad_url, confirm=True)


def test_guard_6_fails_on_protected_database_name(monkeypatch):
    """Dev reset must reject database names containing prod, stage, live."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes_production", confirm=True)

    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/staging_db", confirm=True)


def test_guard_6_fails_on_percent_encoded_database_name(monkeypatch):
    """Dev reset must unquote percent-encoded database names to block obfuscated prod/stage targets."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    # Single percent-encoded 'prod' (%70%72%6f%64)
    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/%70%72%6f%64", confirm=True)

    # Uppercase hex percent-encoded 'prod' (%70%72%6F%64)
    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/%70%72%6F%64", confirm=True)

    # Single percent-encoded 'staging' (%73%74%61%67%69%6e%67)
    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/%73%74%61%67%69%6e%67", confirm=True)

    # Nested double percent-encoded 'prod' (%2570%2572%256f%2564)
    with pytest.raises(ProductionSafetyViolation, match="Guard 6 failed"):
        verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/%2570%2572%256f%2564", confirm=True)


def test_all_guards_pass_on_valid_local_development(monkeypatch):
    """Dev reset safety check passes when all conditions are genuinely met."""
    monkeypatch.setenv("MH_ALLOW_DEV_RESET", "true")
    monkeypatch.setenv("ENV", "development")

    # Should not raise any exception
    verify_safe_for_dev_reset("postgresql://minihermes:pass@127.0.0.1:5433/minihermes_dev", confirm=True)
    verify_safe_for_dev_reset("postgresql+psycopg://minihermes:pass@localhost:5432/minihermes", confirm=True)
