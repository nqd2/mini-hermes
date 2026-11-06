"""Explicit registry for Mini-Hermes tools exposed through Open WebUI's tool loop."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AgentTool:
    handler: Callable
    permission: str
    mutates: bool = False
    requires_preview: bool = False


def get_agent_tools() -> list[Callable]:
    """Return registered handlers; handlers retain Open WebUI's user context and checks."""
    from .tools import (
        cancel_document_task, get_document_task, list_editable_documents, propose_document_edit,
        publish_document_output, read_document_skill, read_editable_document,
        resume_document_task, search_editable_document, start_document_summary_task,
        run_document_code, run_skill_script,
    )

    registry = (
        AgentTool(list_editable_documents, permission='documents.read'),
        AgentTool(read_editable_document, permission='documents.read'),
        AgentTool(search_editable_document, permission='documents.read'),
        AgentTool(start_document_summary_task, permission='documents.read'),
        AgentTool(get_document_task, permission='documents.read'),
        AgentTool(cancel_document_task, permission='documents.read'),
        AgentTool(resume_document_task, permission='documents.read'),
        AgentTool(read_document_skill, permission='documents.read'),
        AgentTool(run_document_code, permission='documents.write', mutates=True),
        AgentTool(run_skill_script, permission='documents.write', mutates=True),
        AgentTool(publish_document_output, permission='documents.write', mutates=True, requires_preview=True),
        AgentTool(propose_document_edit, permission='documents.write', mutates=True, requires_preview=True),
    )
    return [entry.handler for entry in registry]
