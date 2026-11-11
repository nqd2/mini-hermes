"""Version-pinned conversation and run bindings for the canonical runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import HTTPException


CURRENT_ENGINE_TYPE = 'mini-hermes.agent_runtime'
CURRENT_ENGINE_VERSION = 'v1'


@dataclass(frozen=True, slots=True)
class ConversationEngineBinding:
    engine_type: str = CURRENT_ENGINE_TYPE
    engine_version: str = CURRENT_ENGINE_VERSION

    def __post_init__(self) -> None:
        if not self.engine_type.strip() or not self.engine_version.strip():
            raise ValueError('engine_type and engine_version are required')

    @classmethod
    def from_session(cls, session: Any) -> ConversationEngineBinding:
        return cls(engine_type=session.engine_type, engine_version=session.engine_version)


@dataclass(frozen=True, slots=True)
class RunBinding:
    engine_type: str
    engine_version: str
    engine_run_id: str
    engine_thread_id: str
    tool_contract_version: str
    skill_snapshot_version: str

    @classmethod
    def from_records(cls, session: Any, turn: Any) -> RunBinding:
        conversation = ConversationEngineBinding.from_session(session)
        binding = cls(
            engine_type=turn.engine_type,
            engine_version=turn.engine_version,
            engine_run_id=turn.engine_run_id,
            engine_thread_id=turn.engine_thread_id,
            tool_contract_version=turn.tool_contract_version,
            skill_snapshot_version=turn.skill_snapshot_version,
        )
        if (
            binding.engine_type != conversation.engine_type
            or binding.engine_version != conversation.engine_version
            or binding.engine_thread_id != session.id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'runtime_binding_mismatch',
                    'message': 'A run cannot change the engine or thread bound to its conversation.',
                },
            )
        return binding

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def compute_run_snapshot_versions(payload: dict[str, Any]) -> tuple[str, str]:
    """Fingerprint the exact tools and system/developer instructions sent to a provider."""
    if not isinstance(payload, dict):
        raise ValueError('provider payload must be an object')
    messages = payload.get('messages')
    if not isinstance(messages, list):
        messages = []
    skill_snapshot = payload.get('skill_snapshots')
    if skill_snapshot is None:
        skill_snapshot = [
            {'role': message.get('role'), 'content': message.get('content')}
            for message in messages
            if isinstance(message, dict) and message.get('role') in {'system', 'developer'}
        ]
    tool_contract = {'tools': payload.get('tools') or [], 'functions': payload.get('functions') or []}

    def version(value: Any) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
        return f'sha256:{hashlib.sha256(serialized.encode("utf-8")).hexdigest()}'

    return version(tool_contract), version(skill_snapshot)
