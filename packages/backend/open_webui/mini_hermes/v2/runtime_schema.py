"""Runtime SQLAlchemy ``Base.metadata`` table management for V2 greenfield deployments.

Registers all runtime ORM models (Open WebUI async entities, AgentRuntime state,
and Mini-Hermes document/revision tables) so V2 migrations create every ``mh_*``
table without touching V1 Alembic history or V1 table names.
"""

from __future__ import annotations

import importlib

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from open_webui.mini_hermes.v2.db import normalize_v2_db_url

_RUNTIME_MODEL_MODULES: tuple[str, ...] = (
    "open_webui.models.access_grants",
    "open_webui.models.apps",
    "open_webui.models.auths",
    "open_webui.models.automations",
    "open_webui.models.calendar",
    "open_webui.models.channels",
    "open_webui.models.chat_messages",
    "open_webui.models.chats",
    "open_webui.models.comments",
    "open_webui.models.config",
    "open_webui.models.feedbacks",
    "open_webui.models.files",
    "open_webui.models.folders",
    "open_webui.models.function_history",
    "open_webui.models.functions",
    "open_webui.models.groups",
    "open_webui.models.knowledge",
    "open_webui.models.memories",
    "open_webui.models.messages",
    "open_webui.models.model_history",
    "open_webui.models.models",
    "open_webui.models.notes",
    "open_webui.models.notifications",
    "open_webui.models.oauth_sessions",
    "open_webui.models.prompt_history",
    "open_webui.models.prompts",
    "open_webui.models.shared_chats",
    "open_webui.models.skill_history",
    "open_webui.models.skills",
    "open_webui.models.tags",
    "open_webui.models.tool_history",
    "open_webui.models.tools",
    "open_webui.models.users",
    "open_webui.agent_runtime.models",
    "open_webui.mini_hermes.models",
)


def _import_runtime_models() -> None:
    for module_name in _RUNTIME_MODEL_MODULES:
        importlib.import_module(module_name)


def get_runtime_metadata():
    """Import all runtime ORM modules and return ``Base.metadata``."""
    _import_runtime_models()
    from open_webui.internal.db import Base

    return Base.metadata


def ensure_runtime_tables(bind: Connection) -> None:
    """Create all runtime ``Base.metadata`` tables idempotently on ``bind``."""
    metadata = get_runtime_metadata()
    metadata.create_all(bind=bind, checkfirst=True)


def drop_runtime_tables(bind: Connection) -> None:
    """Drop all runtime ``Base.metadata`` tables on ``bind``."""
    metadata = get_runtime_metadata()
    metadata.drop_all(bind=bind, checkfirst=True)


def ensure_runtime_tables_for_url(database_url: str | None = None) -> None:
    """Ensure all runtime ``Base.metadata`` tables exist for ``database_url``."""
    import os

    url = normalize_v2_db_url(database_url or os.getenv("DATABASE_URL"))
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            ensure_runtime_tables(conn)
    finally:
        engine.dispose()
