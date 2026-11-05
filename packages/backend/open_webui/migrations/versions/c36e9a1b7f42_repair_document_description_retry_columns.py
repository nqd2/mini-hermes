"""Repair retry columns if the Personal Folder migration was partially applied."""

from alembic import op
import sqlalchemy as sa


revision = 'c36e9a1b7f42'
down_revision = 'b17d9f3a2c41'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column['name'] for column in sa.inspect(bind).get_columns('mh_document')}

    with op.batch_alter_table('mh_document') as batch:
        if 'description_attempts' not in columns:
            batch.add_column(sa.Column(
                'description_attempts', sa.Integer(), nullable=False, server_default='0'
            ))
        if 'description_next_attempt_at' not in columns:
            batch.add_column(sa.Column(
                'description_next_attempt_at', sa.DateTime(timezone=True),
                nullable=False, server_default=sa.func.now(),
            ))


def downgrade():
    bind = op.get_bind()
    columns = {column['name'] for column in sa.inspect(bind).get_columns('mh_document')}
    with op.batch_alter_table('mh_document') as batch:
        if 'description_next_attempt_at' in columns:
            batch.drop_column('description_next_attempt_at')
        if 'description_attempts' in columns:
            batch.drop_column('description_attempts')
