"""Add Mini-Hermes document revisions and Drive sync jobs."""

from alembic import op
import sqlalchemy as sa

revision = 'a26b11c04d25'
down_revision = 'e42c8f710a36'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mh_document',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('filename', sa.String(512), nullable=False),
        sa.Column('mime_type', sa.String(255), nullable=False),
        sa.Column('current_revision', sa.Integer(), nullable=False),
        sa.Column('drive_file_id', sa.String(255)),
        sa.Column('drive_mime_type', sa.String(255)),
        sa.Column('drive_version', sa.String(255)),
        sa.Column('office_companion_id', sa.String(255)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_mh_document_owner_id', 'mh_document', ['owner_id'])
    op.create_table('mh_revision',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('object_key', sa.String(1024), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('preview_id', sa.String(36)),
        sa.Column('operations', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('document_id', 'number'), sa.UniqueConstraint('preview_id'))
    op.create_index('ix_mh_revision_document_id', 'mh_revision', ['document_id'])
    op.create_table('mh_preview',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('base_revision', sa.Integer(), nullable=False),
        sa.Column('drive_version', sa.String(255)),
        sa.Column('operations', sa.Text(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('preview_key', sa.String(1024)),
        sa.Column('diff', sa.Text()),
        sa.Column('error', sa.Text()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_mh_preview_document_id', 'mh_preview', ['document_id'])
    op.create_table('mh_sync_job',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('revision_id', sa.String(36), sa.ForeignKey('mh_revision.id'), nullable=False, unique=True),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_mh_sync_job_document_id', 'mh_sync_job', ['document_id'])
    op.create_table('mh_drive_credential',
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('encrypted_refresh_token', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('mh_drive_credential')
    op.drop_index('ix_mh_sync_job_document_id', table_name='mh_sync_job')
    op.drop_table('mh_sync_job')
    op.drop_index('ix_mh_preview_document_id', table_name='mh_preview')
    op.drop_table('mh_preview')
    op.drop_index('ix_mh_revision_document_id', table_name='mh_revision')
    op.drop_table('mh_revision')
    op.drop_index('ix_mh_document_owner_id', table_name='mh_document')
    op.drop_table('mh_document')
