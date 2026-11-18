"""0001_v2_baseline

Revision ID: 0001_v2_baseline
Revises: None
Create Date: 2026-11-13

Initial Greenfield baseline migration creating all 49 ar_* tables across 6 domains:
1. Storage & Core Documents (10 tables)
2. Format Canonical Tables (7 tables)
3. Revision & Approvals (5 tables)
4. AgentRuntime Engine (8 tables)
5. Task Workers (5 tables)
6. Auth / RBAC / WeAI Async Entities (14 tables)
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_v2_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _statements() -> list[str]:
    """Read the immutable PostgreSQL DDL snapshot for this revision."""
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    statements = sql.split("\n-- agent-runtime-ddl-boundary --\n")
    statements[0] = statements[0].split("\n", 2)[-1]
    statements = [statement.strip() for statement in statements if statement.strip()]
    if len(statements) != 277:
        raise RuntimeError("V2 baseline DDL snapshot is incomplete")
    return statements


def upgrade() -> None:
    """Create the frozen V2 and runtime schema in a fresh PostgreSQL database."""
    bind = op.get_bind()
    for statement in _statements():
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    """Drop only the tables created by this baseline, in dependency order."""
    bind = op.get_bind()
    names = [
        statement.removeprefix("CREATE TABLE ").split(" (", 1)[0]
        for statement in _statements()
        if statement.startswith("CREATE TABLE ")
    ]
    if len(names) != 101 or any(not name.isidentifier() for name in names):
        raise RuntimeError("V2 baseline table list is incomplete")
    for name in reversed(names):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
