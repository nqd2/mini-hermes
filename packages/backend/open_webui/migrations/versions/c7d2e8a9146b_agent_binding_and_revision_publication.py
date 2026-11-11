"""Add pinned runtime bindings and durable revision publication records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import open_webui.internal.db


revision = 'c7d2e8a9146b'
down_revision = 'bf4c1d7a935e'
branch_labels = None
depends_on = None

ENGINE_TYPE = 'mini-hermes.agent_runtime'
ENGINE_VERSION = 'v1'


def upgrade() -> None:
    op.add_column(
        'agent_session',
        sa.Column('engine_type', sa.String(96), nullable=False, server_default=ENGINE_TYPE),
    )
    op.add_column(
        'agent_session',
        sa.Column('engine_version', sa.String(96), nullable=False, server_default=ENGINE_VERSION),
    )

    op.add_column('agent_turn', sa.Column('idempotency_key', sa.String(255), nullable=True))
    op.add_column(
        'agent_turn',
        sa.Column('engine_type', sa.String(96), nullable=False, server_default=ENGINE_TYPE),
    )
    op.add_column(
        'agent_turn',
        sa.Column('engine_version', sa.String(96), nullable=False, server_default=ENGINE_VERSION),
    )
    op.add_column(
        'agent_turn',
        sa.Column('engine_run_id', sa.String(255), nullable=False, server_default='legacy-unbound'),
    )
    op.add_column(
        'agent_turn',
        sa.Column('engine_thread_id', sa.String(255), nullable=False, server_default='legacy-unbound'),
    )
    op.add_column(
        'agent_turn',
        sa.Column('tool_contract_version', sa.String(255), nullable=False, server_default='legacy-unpinned'),
    )
    op.add_column(
        'agent_turn',
        sa.Column('skill_snapshot_version', sa.String(255), nullable=False, server_default='legacy-unpinned'),
    )
    op.add_column(
        'agent_turn',
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table('agent_turn') as batch:
        batch.create_unique_constraint('uq_agent_turn_session_idempotency', ['session_id', 'idempotency_key'])
        batch.drop_constraint('ck_agent_turn_status', type_='check')
        batch.create_check_constraint(
            'ck_agent_turn_status',
            "status IN ('queued', 'running', 'waiting_approval', 'paused', 'completed', 'failed', "
            "'interrupted', 'cancelled')",
        )

    # Give each existing run a deterministic binding while retaining all legacy state.
    if not op.get_context().as_sql:
        bind = op.get_bind()
        turns = sa.table(
            'agent_turn',
            sa.column('id', sa.String),
            sa.column('session_id', sa.String),
            sa.column('engine_run_id', sa.String),
            sa.column('engine_thread_id', sa.String),
        )
        for turn_id, session_id in bind.execute(sa.select(turns.c.id, turns.c.session_id)):
            bind.execute(
                turns.update()
                .where(turns.c.id == turn_id)
                .values(
                    engine_run_id=f'legacy:{turn_id}',
                    engine_thread_id=f'legacy:{session_id}',
                )
            )

    with op.batch_alter_table('agent_turn') as batch:
        batch.alter_column('engine_run_id', server_default=None)
        batch.alter_column('engine_thread_id', server_default=None)
        batch.alter_column('engine_type', server_default=None)
        batch.alter_column('engine_version', server_default=None)
        batch.alter_column('tool_contract_version', server_default=None)
        batch.alter_column('skill_snapshot_version', server_default=None)
        batch.alter_column('cancel_requested', server_default=None)
    with op.batch_alter_table('agent_session') as batch:
        batch.alter_column('engine_type', server_default=None)
        batch.alter_column('engine_version', server_default=None)

    with op.batch_alter_table('mh_preview') as batch:
        batch.add_column(sa.Column('patch_hash', sa.String(64), nullable=True))
    with op.batch_alter_table('mh_revision') as batch:
        batch.add_column(sa.Column('idempotency_key', sa.String(255), nullable=True))
        batch.create_unique_constraint('uq_mh_revision_idempotency_key', ['idempotency_key'])

    json_field = open_webui.internal.db.JSONField
    op.create_table(
        'mh_revision_approval',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('preview_id', sa.String(36), sa.ForeignKey('mh_preview.id', ondelete='SET NULL')),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('resource_id', sa.String(36), sa.ForeignKey('mh_document.id', ondelete='CASCADE'), nullable=False),
        sa.Column('base_revision', sa.Integer(), nullable=False),
        sa.Column('patch_hash', sa.String(64), nullable=False),
        sa.Column('approver_id', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('idempotency_key', name='uq_mh_revision_approval_idempotency'),
        sa.CheckConstraint('expires_at > created_at', name='ck_mh_revision_approval_expiration'),
    )
    for name, column in (
        ('ix_mh_revision_approval_preview_id', 'preview_id'),
        ('ix_mh_revision_approval_user_id', 'user_id'),
        ('ix_mh_revision_approval_resource_id', 'resource_id'),
        ('ix_mh_revision_approval_approver_id', 'approver_id'),
    ):
        op.create_index(name, 'mh_revision_approval', [column])

    op.create_table(
        'mh_revision_outbox',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('event_key', sa.String(255), nullable=False),
        sa.Column('document_id', sa.String(36), sa.ForeignKey('mh_document.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision_id', sa.String(36), sa.ForeignKey('mh_revision.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(96), nullable=False),
        sa.Column('payload', json_field(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('event_key', name='uq_mh_revision_outbox_event_key'),
    )
    for name, column in (
        ('ix_mh_revision_outbox_document_id', 'document_id'),
        ('ix_mh_revision_outbox_revision_id', 'revision_id'),
    ):
        op.create_index(name, 'mh_revision_outbox', [column])
    op.create_index(
        'ix_mh_revision_outbox_dispatch',
        'mh_revision_outbox',
        ['published_at', 'lease_until', 'created_at'],
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError('Downgrade requires an online check for durable runtime and revision data.')
    bind = op.get_bind()
    for table_name in ('agent_session', 'mh_revision_approval', 'mh_revision_outbox'):
        if table_name in sa.inspect(bind).get_table_names():
            table = sa.table(table_name)
            if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
                raise RuntimeError(
                    'Refusing to remove agent bindings or revision approval/outbox data; '
                    'restore an application version that tolerates additive columns instead.'
                )
    if 'mh_revision' in sa.inspect(bind).get_table_names():
        revisions = sa.table('mh_revision', sa.column('idempotency_key', sa.String))
        if bind.execute(
            sa.select(sa.literal(1)).select_from(revisions).where(revisions.c.idempotency_key.is_not(None)).limit(1)
        ).first():
            raise RuntimeError('Refusing to remove revision idempotency data.')

    op.drop_index('ix_mh_revision_outbox_dispatch', table_name='mh_revision_outbox')
    op.drop_index('ix_mh_revision_outbox_revision_id', table_name='mh_revision_outbox')
    op.drop_index('ix_mh_revision_outbox_document_id', table_name='mh_revision_outbox')
    op.drop_table('mh_revision_outbox')
    op.drop_index('ix_mh_revision_approval_approver_id', table_name='mh_revision_approval')
    op.drop_index('ix_mh_revision_approval_resource_id', table_name='mh_revision_approval')
    op.drop_index('ix_mh_revision_approval_user_id', table_name='mh_revision_approval')
    op.drop_index('ix_mh_revision_approval_preview_id', table_name='mh_revision_approval')
    op.drop_table('mh_revision_approval')
    with op.batch_alter_table('mh_revision') as batch:
        batch.drop_constraint('uq_mh_revision_idempotency_key', type_='unique')
        batch.drop_column('idempotency_key')
    with op.batch_alter_table('mh_preview') as batch:
        batch.drop_column('patch_hash')
    with op.batch_alter_table('agent_turn') as batch:
        batch.drop_constraint('ck_agent_turn_status', type_='check')
        batch.create_check_constraint(
            'ck_agent_turn_status',
            "status IN ('queued', 'running', 'waiting_approval', 'paused', 'completed', 'failed', 'interrupted')",
        )
        batch.drop_constraint('uq_agent_turn_session_idempotency', type_='unique')
        for column in (
            'cancel_requested',
            'skill_snapshot_version',
            'tool_contract_version',
            'engine_thread_id',
            'engine_run_id',
            'engine_version',
            'engine_type',
            'idempotency_key',
        ):
            batch.drop_column(column)
    with op.batch_alter_table('agent_session') as batch:
        batch.drop_column('engine_version')
        batch.drop_column('engine_type')
