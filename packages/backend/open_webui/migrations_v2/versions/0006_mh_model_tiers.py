"""Add Mini-Hermes model tiers to the V2 bootstrap path."""

from alembic import op
from open_webui.mini_hermes import tier_schema_v1

revision = '0006_mh_model_tiers'
down_revision = '0005_v2_user_input_wait'
branch_labels = None
depends_on = None


def upgrade():
    tier_schema_v1.upgrade(op)


def downgrade():
    tier_schema_v1.downgrade(op)
