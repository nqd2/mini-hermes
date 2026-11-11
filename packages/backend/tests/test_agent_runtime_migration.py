from __future__ import annotations

import importlib

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from open_webui.agent_runtime.models import AgentSession, Base


def test_downgrade_refuses_to_drop_durable_runtime_data():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AgentSession(id='session-1', owner_id='owner-1', chat_id='chat-1', task_state={}))
        session.commit()

    migration = importlib.import_module(
        'open_webui.migrations.versions.bf4c1d7a935e_add_agent_runtime_state'
    )
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(RuntimeError, match='agent runtime data'):
            migration.downgrade()

    assert inspect(engine).has_table('agent_session')
    engine.dispose()


def test_empty_runtime_schema_upgrades_downgrades_and_reupgrades():
    engine = create_engine('sqlite://')
    migration = importlib.import_module(
        'open_webui.migrations.versions.bf4c1d7a935e_add_agent_runtime_state'
    )
    runtime_tables = {
        'agent_session', 'agent_turn', 'agent_event', 'agent_checkpoint',
        'agent_tool_invocation', 'agent_artifact', 'agent_file_content',
    }

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        assert runtime_tables.issubset(set(inspect(connection).get_table_names()))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.downgrade()
        assert runtime_tables.isdisjoint(set(inspect(connection).get_table_names()))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        assert runtime_tables.issubset(set(inspect(connection).get_table_names()))

    engine.dispose()


def test_runtime_and_revision_refinement_migration_backfills_bindings_and_creates_publication_tables():
    engine = create_engine('sqlite://')
    base_migration = importlib.import_module(
        'open_webui.migrations.versions.bf4c1d7a935e_add_agent_runtime_state'
    )
    refinement = importlib.import_module(
        'open_webui.migrations.versions.c7d2e8a9146b_agent_binding_and_revision_publication'
    )

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            base_migration.upgrade()
        connection.exec_driver_sql(
            'CREATE TABLE mh_document (id VARCHAR(36) PRIMARY KEY, current_revision INTEGER NOT NULL)'
        )
        connection.exec_driver_sql(
            'CREATE TABLE mh_revision (id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL, '
            'number INTEGER NOT NULL, object_key VARCHAR(1024) NOT NULL, sha256 VARCHAR(64) NOT NULL, '
            'size INTEGER NOT NULL, preview_id VARCHAR(36), operations TEXT, created_at DATETIME NOT NULL)'
        )
        connection.exec_driver_sql(
            'CREATE TABLE mh_preview (id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL, '
            'owner_id VARCHAR(255) NOT NULL, base_revision INTEGER NOT NULL, drive_version VARCHAR(255), '
            'operations TEXT NOT NULL, status VARCHAR(24) NOT NULL, preview_key VARCHAR(1024), diff TEXT, '
            'error TEXT, lease_until DATETIME, created_at DATETIME NOT NULL)'
        )
        connection.exec_driver_sql(
            "INSERT INTO agent_session (id, owner_id, chat_id, status, next_turn_sequence, created_at, updated_at) "
            "VALUES ('session-1', 'owner-1', 'chat-1', 'active', 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO agent_turn (id, session_id, sequence, status, request_payload, attempts, "
            "next_event_sequence, checkpoint_version, created_at, updated_at) VALUES "
            "('turn-1', 'session-1', 1, 'interrupted', '{}', 1, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            refinement.upgrade()

        inspector = inspect(connection)
        assert {'mh_revision_approval', 'mh_revision_outbox'}.issubset(set(inspector.get_table_names()))
        assert {'patch_hash'}.issubset({column['name'] for column in inspector.get_columns('mh_preview')})
        assert {'idempotency_key'}.issubset({column['name'] for column in inspector.get_columns('mh_revision')})
        row = connection.exec_driver_sql(
            'SELECT engine_type, engine_version FROM agent_session WHERE id = ?', ('session-1',)
        ).one()
        turn = connection.exec_driver_sql(
            'SELECT engine_run_id, engine_thread_id, tool_contract_version, skill_snapshot_version '
            'FROM agent_turn WHERE id = ?', ('turn-1',)
        ).one()
        assert tuple(row) == ('mini-hermes.agent_runtime', 'v1')
        assert tuple(turn) == ('legacy:turn-1', 'legacy:session-1', 'legacy-unpinned', 'legacy-unpinned')

        # Empty-schema downgrade/re-upgrade is safe; populated state is covered by the refusal test above.
        connection.exec_driver_sql("DELETE FROM agent_turn WHERE id = 'turn-1'")
        connection.exec_driver_sql("DELETE FROM agent_session WHERE id = 'session-1'")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            refinement.downgrade()
        assert {'mh_revision_approval', 'mh_revision_outbox'}.isdisjoint(set(inspect(connection).get_table_names()))
        assert 'patch_hash' not in {column['name'] for column in inspect(connection).get_columns('mh_preview')}

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            refinement.upgrade()
        assert {'mh_revision_approval', 'mh_revision_outbox'}.issubset(set(inspect(connection).get_table_names()))

    engine.dispose()
