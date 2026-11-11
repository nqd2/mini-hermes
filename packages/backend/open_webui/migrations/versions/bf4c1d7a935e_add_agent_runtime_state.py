"""Add durable state for the canonical agent runtime."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import open_webui.internal.db


revision: str = 'bf4c1d7a935e'
down_revision: Union[str, tuple[str, str], None] = ('3c9b0ca343fd', 'd8e9f0a1b2c3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_field = open_webui.internal.db.JSONField
    op.create_table(
        'agent_session',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('owner_id', sa.String(255), nullable=False),
        sa.Column('chat_id', sa.String(255)),
        sa.Column('parent_session_id', sa.String(36)),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('next_turn_sequence', sa.Integer(), nullable=False),
        sa.Column('task_state', json_field()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_session_id'], ['agent_session.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'chat_id', name='uq_agent_session_owner_chat'),
    )
    for name, columns in (
        ('ix_agent_session_chat_id', ['chat_id']),
        ('ix_agent_session_owner_id', ['owner_id']),
        ('ix_agent_session_parent_session_id', ['parent_session_id']),
        ('ix_agent_session_owner_updated', ['owner_id', 'updated_at']),
    ):
        op.create_index(name, 'agent_session', columns)

    op.create_table(
        'agent_turn',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('request_payload', json_field(), nullable=False),
        sa.Column('model_id', sa.String(255)),
        sa.Column('chat_message_id', sa.String(255)),
        sa.Column('user_message_id', sa.String(255)),
        sa.Column('source_message_id', sa.String(255)),
        sa.Column('worker_id', sa.String(255)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_event_sequence', sa.Integer(), nullable=False),
        sa.Column('checkpoint_version', sa.Integer(), nullable=False),
        sa.Column('error', json_field()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'paused', 'completed', 'failed', 'interrupted')",
            name='ck_agent_turn_status',
        ),
        sa.ForeignKeyConstraint(['session_id'], ['agent_session.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'sequence', name='uq_agent_turn_session_sequence'),
        sa.UniqueConstraint('session_id', 'source_message_id', name='uq_agent_turn_session_source_message'),
    )
    for name, columns in (
        ('ix_agent_turn_session_id', ['session_id']),
        ('ix_agent_turn_status', ['status']),
        ('ix_agent_turn_chat_message_id', ['chat_message_id']),
        ('ix_agent_turn_session_chat_message', ['session_id', 'chat_message_id']),
        ('ix_agent_turn_claim', ['status', 'created_at', 'lease_until']),
    ):
        op.create_index(name, 'agent_turn', columns)

    op.create_table(
        'agent_event',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('turn_id', sa.String(36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(96), nullable=False),
        sa.Column('payload', json_field(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['agent_session.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['agent_turn.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('turn_id', 'sequence', name='uq_agent_event_turn_sequence'),
    )
    for name, columns in (
        ('ix_agent_event_session_id', ['session_id']),
        ('ix_agent_event_turn_id', ['turn_id']),
        ('ix_agent_event_outbox', ['published_at', 'created_at']),
    ):
        op.create_index(name, 'agent_event', columns)

    op.create_table(
        'agent_checkpoint',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('turn_id', sa.String(36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('data', json_field(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['agent_session.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['agent_turn.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('turn_id', 'version', name='uq_agent_checkpoint_turn_version'),
    )
    for name, columns in (
        ('ix_agent_checkpoint_session_id', ['session_id']),
        ('ix_agent_checkpoint_turn_id', ['turn_id']),
        ('ix_agent_checkpoint_session_created', ['session_id', 'created_at']),
    ):
        op.create_index(name, 'agent_checkpoint', columns)

    op.create_table(
        'agent_tool_invocation',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('turn_id', sa.String(36), nullable=False),
        sa.Column('call_id', sa.String(255), nullable=False),
        sa.Column('tool_name', sa.String(255), nullable=False),
        sa.Column('arguments', json_field(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('retry_safe', sa.Boolean(), nullable=False),
        sa.Column('idempotency_key', sa.String(255)),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('result_data', json_field()),
        sa.Column('result_artifact_id', sa.String(36)),
        sa.Column('error', json_field()),
        sa.Column('approval_status', sa.String(24)),
        sa.Column('approved_by', sa.String(255)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', 'failed', 'outcome_unknown')",
            name='ck_agent_tool_status',
        ),
        sa.ForeignKeyConstraint(['session_id'], ['agent_session.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['agent_turn.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('turn_id', 'call_id', name='uq_agent_tool_turn_call'),
    )
    for name, columns in (
        ('ix_agent_tool_invocation_session_id', ['session_id']),
        ('ix_agent_tool_invocation_turn_id', ['turn_id']),
        ('ix_agent_tool_invocation_status', ['status']),
        ('ix_agent_tool_turn_status', ['turn_id', 'status']),
    ):
        op.create_index(name, 'agent_tool_invocation', columns)

    op.create_table(
        'agent_artifact',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('owner_id', sa.String(255), nullable=False),
        sa.Column('session_id', sa.String(36)),
        sa.Column('turn_id', sa.String(36)),
        sa.Column('object_key', sa.String(1024), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('mime_type', sa.String(255), nullable=False),
        sa.Column('filename', sa.String(512)),
        sa.Column('metadata_json', json_field()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['agent_session.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['turn_id'], ['agent_turn.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('object_key', name='uq_agent_artifact_object_key'),
    )
    for name, columns in (
        ('ix_agent_artifact_owner_id', ['owner_id']),
        ('ix_agent_artifact_owner_created', ['owner_id', 'created_at']),
        ('ix_agent_artifact_turn', ['turn_id']),
    ):
        op.create_index(name, 'agent_artifact', columns)

    op.create_table(
        'agent_file_content',
        sa.Column('file_id', sa.String(255), nullable=False),
        sa.Column('owner_id', sa.String(255), nullable=False),
        sa.Column('object_key', sa.String(1024), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('char_count', sa.BigInteger(), nullable=False),
        sa.Column('line_count', sa.BigInteger(), nullable=False),
        sa.Column('line_index', json_field(), nullable=False),
        sa.Column('char_index', json_field(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('file_id'),
        sa.UniqueConstraint('object_key', name='uq_agent_file_content_object_key'),
    )
    op.create_index('ix_agent_file_content_owner_id', 'agent_file_content', ['owner_id'])
    op.create_index('ix_agent_file_content_owner_created', 'agent_file_content', ['owner_id', 'created_at'])


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError('Agent runtime downgrade requires an online empty database.')
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    runtime_tables = (
        'agent_session',
        'agent_turn',
        'agent_event',
        'agent_checkpoint',
        'agent_tool_invocation',
        'agent_artifact',
        'agent_file_content',
    )
    populated = []
    for table_name in runtime_tables:
        if table_name not in existing_tables:
            continue
        table = sa.table(table_name)
        if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
            populated.append(table_name)
    if populated:
        raise RuntimeError(
            'Refusing to drop agent runtime data. Keep the additive schema during application rollback; '
            f'populated tables: {", ".join(populated)}.'
        )

    for name in ('ix_agent_file_content_owner_created', 'ix_agent_file_content_owner_id'):
        op.drop_index(name, table_name='agent_file_content')
    op.drop_table('agent_file_content')
    for name in ('ix_agent_artifact_turn', 'ix_agent_artifact_owner_created', 'ix_agent_artifact_owner_id'):
        op.drop_index(name, table_name='agent_artifact')
    op.drop_table('agent_artifact')
    for name in ('ix_agent_tool_turn_status', 'ix_agent_tool_invocation_status', 'ix_agent_tool_invocation_turn_id', 'ix_agent_tool_invocation_session_id'):
        op.drop_index(name, table_name='agent_tool_invocation')
    op.drop_table('agent_tool_invocation')
    for name in ('ix_agent_checkpoint_session_created', 'ix_agent_checkpoint_turn_id', 'ix_agent_checkpoint_session_id'):
        op.drop_index(name, table_name='agent_checkpoint')
    op.drop_table('agent_checkpoint')
    for name in ('ix_agent_event_outbox', 'ix_agent_event_turn_id', 'ix_agent_event_session_id'):
        op.drop_index(name, table_name='agent_event')
    op.drop_table('agent_event')
    for name in (
        'ix_agent_turn_claim', 'ix_agent_turn_session_chat_message', 'ix_agent_turn_chat_message_id',
        'ix_agent_turn_status', 'ix_agent_turn_session_id',
    ):
        op.drop_index(name, table_name='agent_turn')
    op.drop_table('agent_turn')
    for name in (
        'ix_agent_session_owner_updated',
        'ix_agent_session_parent_session_id',
        'ix_agent_session_owner_id',
        'ix_agent_session_chat_id',
    ):
        op.drop_index(name, table_name='agent_session')
    op.drop_table('agent_session')
