"""0001_v2_baseline

Revision ID: 0001_v2_baseline
Revises: None
Create Date: 2026-11-13

Initial Greenfield baseline migration creating all 49 mh_* tables across 6 domains:
1. Storage & Core Documents (10 tables)
2. Format Canonical Tables (7 tables)
3. Revision & Approvals (5 tables)
4. AgentRuntime Engine (8 tables)
5. Task Workers (5 tables)
6. Auth / RBAC / Open WebUI Async Entities (14 tables)
"""

from typing import Sequence, Union

from alembic import op
from open_webui.mini_hermes.v2.models import v2_metadata
from open_webui.mini_hermes.v2.runtime_schema import (
    drop_runtime_tables,
    ensure_runtime_tables,
)

# revision identifiers, used by Alembic.
revision: str = "0001_v2_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all V2 mh_* and runtime mh_* tables cleanly."""
    bind = op.get_bind()
    v2_metadata.create_all(bind=bind)
    ensure_runtime_tables(bind)


def downgrade() -> None:
    """Drop all runtime mh_* and V2 mh_* tables cleanly."""
    bind = op.get_bind()
    drop_runtime_tables(bind)
    v2_metadata.drop_all(bind=bind)
