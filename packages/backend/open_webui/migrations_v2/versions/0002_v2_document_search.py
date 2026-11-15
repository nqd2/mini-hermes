"""Provision PostgreSQL search for the runtime document chunks."""

from alembic import op

revision = '0002_v2_document_search'
down_revision = '0001_v2_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public')
    op.execute("CREATE INDEX IF NOT EXISTS ix_mh_chunk_fts ON mh_document_chunk USING gin (to_tsvector('simple', content))")
    op.execute('CREATE INDEX IF NOT EXISTS ix_mh_chunk_trgm ON mh_document_chunk USING gin (content public.gin_trgm_ops)')


def downgrade() -> None:
    # pg_trgm is shared across schemas and can be used by other features.
    op.drop_index('ix_mh_chunk_trgm', table_name='mh_document_chunk')
    op.drop_index('ix_mh_chunk_fts', table_name='mh_document_chunk')
