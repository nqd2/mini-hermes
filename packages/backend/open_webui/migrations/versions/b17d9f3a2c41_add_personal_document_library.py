"""Add personal folders and chat-scoped Mini-Hermes document access."""

from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa


revision = 'b17d9f3a2c41'
down_revision = 'a26b11c04d25'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mh_folder',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('parent_id', sa.String(36), sa.ForeignKey('mh_folder.id')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('is_personal_root', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mh_folder_owner_id', 'mh_folder', ['owner_id'])
    op.create_index('ix_mh_folder_parent_id', 'mh_folder', ['parent_id'])
    op.create_index(
        'uq_mh_folder_personal_root', 'mh_folder', ['owner_id'], unique=True,
        postgresql_where=sa.text('is_personal_root = true'),
        sqlite_where=sa.text('is_personal_root = 1'),
    )

    with op.batch_alter_table('mh_document') as batch:
        batch.add_column(sa.Column('folder_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch.add_column(sa.Column('description_status', sa.String(24), nullable=False, server_default='pending'))
        batch.add_column(sa.Column('description_error', sa.Text(), nullable=True))
        batch.add_column(sa.Column('description_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('description_next_attempt_at', sa.DateTime(timezone=True), nullable=False,
                                  server_default=sa.func.now()))
        batch.add_column(sa.Column('source_file_id', sa.String(255), nullable=True))
        batch.create_foreign_key('fk_mh_document_folder_id', 'mh_folder', ['folder_id'], ['id'])
    op.create_index('ix_mh_document_folder_id', 'mh_document', ['folder_id'])
    op.create_index('uq_mh_document_source_file_id', 'mh_document', ['source_file_id'], unique=True)

    connection = op.get_bind()
    owners = connection.execute(sa.text('SELECT DISTINCT owner_id FROM mh_document')).scalars().all()
    for owner_id in owners:
        root_id = str(uuid5(NAMESPACE_URL, f'mini-hermes:personal:{owner_id}'))
        connection.execute(
            sa.text("INSERT INTO mh_folder (id, owner_id, parent_id, name, is_personal_root, created_at) "
                    "VALUES (:id, :owner_id, NULL, 'Personal', TRUE, CURRENT_TIMESTAMP)"),
            {'id': root_id, 'owner_id': owner_id},
        )
        connection.execute(
            sa.text('UPDATE mh_document SET folder_id = :folder_id WHERE owner_id = :owner_id'),
            {'folder_id': root_id, 'owner_id': owner_id},
        )

    op.create_table(
        'mh_chat_resource',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('chat_id', sa.String(255), nullable=False),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id'), nullable=False),
        sa.Column('granted_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('chat_id', 'document_id'),
    )
    op.create_index('ix_mh_chat_resource_chat_id', 'mh_chat_resource', ['chat_id'])
    op.create_index('ix_mh_chat_resource_document_id', 'mh_chat_resource', ['document_id'])
    op.create_index('ix_mh_chat_resource_granted_by', 'mh_chat_resource', ['granted_by'])


def downgrade():
    op.drop_table('mh_chat_resource')
    op.drop_index('uq_mh_document_source_file_id', table_name='mh_document')
    op.drop_index('ix_mh_document_folder_id', table_name='mh_document')
    with op.batch_alter_table('mh_document') as batch:
        batch.drop_constraint('fk_mh_document_folder_id', type_='foreignkey')
        batch.drop_column('source_file_id')
        batch.drop_column('description_error')
        batch.drop_column('description_next_attempt_at')
        batch.drop_column('description_attempts')
        batch.drop_column('description_status')
        batch.drop_column('description')
        batch.drop_column('folder_id')
    op.drop_index('uq_mh_folder_personal_root', table_name='mh_folder')
    op.drop_index('ix_mh_folder_parent_id', table_name='mh_folder')
    op.drop_index('ix_mh_folder_owner_id', table_name='mh_folder')
    op.drop_table('mh_folder')
