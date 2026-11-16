"""Give saved user questions a distinct durable AgentRuntime state.

Revision ID: 0005_v2_user_input_wait
Revises: 0004_v2_analysis_trace
"""

from alembic import op


revision = "0005_v2_user_input_wait"
down_revision = "0004_v2_analysis_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_agent_turn_status"), "mh_agent_turn", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_turn_status"),
        "mh_agent_turn",
        "status IN ('queued', 'running', 'waiting_approval', 'waiting_user_input', "
        "'paused', 'completed', 'failed', 'interrupted', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_agent_turn_status"), "mh_agent_turn", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_turn_status"),
        "mh_agent_turn",
        "status IN ('queued', 'running', 'waiting_approval', 'paused', "
        "'completed', 'failed', 'interrupted', 'cancelled')",
    )
