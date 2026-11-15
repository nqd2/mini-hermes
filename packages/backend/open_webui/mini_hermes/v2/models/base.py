"""V2 Declarative Base, naming conventions, and shared type helpers."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# Standard naming conventions for robust PostgreSQL DDL generation
V2_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

v2_metadata = MetaData(naming_convention=V2_NAMING_CONVENTION)


class V2Base(DeclarativeBase):
    """Declarative base class isolated exclusively for Mini-Hermes V2 models."""

    metadata = v2_metadata


# Native JSONB on PostgreSQL; fallback to generic JSON for dialect-neutral mocking
JSONType: Any = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def enforce_postgresql(dialect_name: str) -> None:
    """Refuse non-PostgreSQL dialects.

    Mini-Hermes V2 greenfield persistence strictly requires PostgreSQL.
    SQLite or other engines are rejected to prevent corrupting persistence invariants.
    """
    if dialect_name != "postgresql":
        raise RuntimeError(
            f"Mini-Hermes V2 strictly requires PostgreSQL (got dialect '{dialect_name}'). "
            "SQLite is forbidden for V2 persistence."
        )
