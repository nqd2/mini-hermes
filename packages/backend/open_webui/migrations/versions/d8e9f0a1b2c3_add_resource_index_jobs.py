"""Add durable jobs for indexing immutable document revisions."""

from alembic import op
import sqlalchemy as sa


revision = 'd8e9f0a1b2c3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mh_resource_index_job',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('document_id', 'revision', name='uq_mh_resource_index_document_revision'),
    )
    op.create_index('ix_mh_resource_index_job_document_id', 'mh_resource_index_job', ['document_id'])
    op.create_index('ix_mh_resource_index_job_status', 'mh_resource_index_job', ['status'])
    op.create_index(
        'ix_mh_resource_index_status_next_attempt',
        'mh_resource_index_job',
        ['status', 'next_attempt_at'],
    )


def downgrade():
    op.drop_index('ix_mh_resource_index_status_next_attempt', table_name='mh_resource_index_job')
    op.drop_index('ix_mh_resource_index_job_status', table_name='mh_resource_index_job')
    op.drop_index('ix_mh_resource_index_job_document_id', table_name='mh_resource_index_job')
    op.drop_table('mh_resource_index_job')
