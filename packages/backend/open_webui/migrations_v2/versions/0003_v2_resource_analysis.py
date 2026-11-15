"""Bind document analysis jobs to chat messages and store located evidence."""

from alembic import op

revision = '0003_v2_resource_analysis'
down_revision = '0002_v2_document_search'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from open_webui.mini_hermes.models import ResourceAnalysis, ResourceAnalysisEvidence

    # Fresh baseline provisioning uses the current runtime metadata.
    bind = op.get_bind()
    ResourceAnalysis.__table__.create(bind, checkfirst=True)
    ResourceAnalysisEvidence.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    op.drop_table('mh_resource_analysis_evidence')
    op.drop_table('mh_resource_analysis')
