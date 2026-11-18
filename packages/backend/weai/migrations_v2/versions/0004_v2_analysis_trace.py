"""Add a durable event stream for document analysis steps."""

from alembic import op

revision = '0004_v2_analysis_trace'
down_revision = '0003_v2_resource_analysis'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from weai.documents.models import ResourceAnalysisTrace

    ResourceAnalysisTrace.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table('ar_resource_analysis_trace')
