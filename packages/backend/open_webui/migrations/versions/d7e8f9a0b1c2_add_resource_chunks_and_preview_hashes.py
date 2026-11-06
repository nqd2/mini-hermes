"""Add versioned document search chunks and approval hashes."""

from alembic import op
import sqlalchemy as sa


revision = 'd7e8f9a0b1c2'
down_revision = 'c36e9a1b7f42'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mh_preview') as batch:
        batch.add_column(sa.Column('base_sha256', sa.String(64), nullable=True))
        batch.add_column(sa.Column('artifact_sha256', sa.String(64), nullable=True))
        batch.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'mh_document_chunk',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('source_kind', sa.String(24), nullable=False, server_default='text'),
        sa.Column('section', sa.String(512), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('start_offset', sa.Integer(), nullable=False),
        sa.Column('end_offset', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.UniqueConstraint('document_id', 'revision', 'ordinal', name='uq_mh_chunk_document_revision_ordinal'),
    )
    op.create_index('ix_mh_document_chunk_document_id', 'mh_document_chunk', ['document_id'])
    op.create_index('ix_mh_document_chunk_revision', 'mh_document_chunk', ['revision'])

    op.create_table(
        'mh_resource_task',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('chat_id', sa.String(255), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False, server_default='summary'),
        sa.Column('status', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('phase', sa.String(24), nullable=False, server_default='chunks'),
        sa.Column('next_ordinal', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_group', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('level', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completed_units', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_units', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('checkpoint', sa.Text(), nullable=True),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mh_resource_task_document_id', 'mh_resource_task', ['document_id'])
    op.create_index('ix_mh_resource_task_owner_id', 'mh_resource_task', ['owner_id'])
    op.create_index('ix_mh_resource_task_chat_id', 'mh_resource_task', ['chat_id'])
    op.create_index('ix_mh_resource_task_status', 'mh_resource_task', ['status'])

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        op.execute("CREATE INDEX ix_mh_chunk_fts ON mh_document_chunk USING gin (to_tsvector('simple', content))")
        op.execute("CREATE INDEX ix_mh_chunk_trgm ON mh_document_chunk USING gin (content gin_trgm_ops)")


def downgrade():
    op.drop_index('ix_mh_resource_task_status', table_name='mh_resource_task')
    op.drop_index('ix_mh_resource_task_chat_id', table_name='mh_resource_task')
    op.drop_index('ix_mh_resource_task_owner_id', table_name='mh_resource_task')
    op.drop_index('ix_mh_resource_task_document_id', table_name='mh_resource_task')
    op.drop_table('mh_resource_task')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_mh_chunk_trgm')
        op.execute('DROP INDEX IF EXISTS ix_mh_chunk_fts')
    op.drop_index('ix_mh_document_chunk_revision', table_name='mh_document_chunk')
    op.drop_index('ix_mh_document_chunk_document_id', table_name='mh_document_chunk')
    op.drop_table('mh_document_chunk')
    with op.batch_alter_table('mh_preview') as batch:
        batch.drop_column('approved_at')
        batch.drop_column('artifact_sha256')
        batch.drop_column('base_sha256')
