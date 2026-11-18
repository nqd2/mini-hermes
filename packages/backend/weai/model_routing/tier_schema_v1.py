"""Versioned table definitions shared by the V2 migration and the runtime repository."""

import sqlalchemy as sa


def tier_tables(metadata):
    plans = sa.Table(
        'ar_user_plan',
        metadata,
        sa.Column('user_id', sa.String, sa.ForeignKey('ar_ow_user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tier', sa.String(4), nullable=False),
        sa.Column('monthly_request_limit', sa.Integer),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.String),
        sa.CheckConstraint(
            "(tier = 'free' AND monthly_request_limit IS NULL) OR "
            "(tier = 'plus' AND monthly_request_limit IS NOT NULL AND monthly_request_limit > 0)",
            name='ar_user_plan_valid_limit',
        ),
    )
    dispatches = sa.Table(
        'ar_llm_dispatch',
        metadata,
        sa.Column('dispatch_key', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.String, sa.ForeignKey('ar_ow_user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('group_id', sa.String(32), nullable=False),
        sa.Column('model_id', sa.String, nullable=False),
        sa.Column('connection_id', sa.String, nullable=False),
        sa.Column('free_account', sa.String),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('run_id', sa.String),
        sa.Column('state', sa.String(12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('provider_status', sa.Integer),
        sa.Column('shared_reset_at', sa.DateTime(timezone=True)),
        sa.Column('provider_request_id', sa.String(255)),
        sa.Column('latency_ms', sa.Integer),
        sa.Index('ix_ar_llm_dispatch_user_window', 'user_id', 'group_id', 'created_at'),
        sa.Index('ix_ar_llm_dispatch_free_window', 'free_account', 'created_at'),
    )
    selections = sa.Table(
        'ar_run_model_selection',
        metadata,
        sa.Column('user_id', sa.String, sa.ForeignKey('ar_ow_user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('run_id', sa.String, primary_key=True),
        sa.Column('group_id', sa.String(32), nullable=False),
        sa.Column('connection_id', sa.String, nullable=False),
        sa.Column('model_id', sa.String, nullable=False),
        sa.Column('pool_version', sa.Integer, nullable=False),
    )
    return plans, dispatches, selections


def upgrade(op):
    metadata = sa.MetaData()
    sa.Table('ar_ow_user', metadata, sa.Column('id', sa.String, primary_key=True))
    metadata.create_all(op.get_bind(), tables=tier_tables(metadata), checkfirst=False)
    op.add_column('ar_resource_task', sa.Column('logical_model_id', sa.String(32)))


def downgrade(op):
    op.drop_column('ar_resource_task', 'logical_model_id')
    for table in ('ar_run_model_selection', 'ar_llm_dispatch', 'ar_user_plan'):
        op.drop_table(table)
