"""Resolve request-scoped document references before chat context construction."""

from dataclasses import dataclass

from open_webui.mini_hermes.agent_mode import document_agent_mode
from open_webui.mini_hermes.resource_aware import resource_aware_context_enabled


@dataclass(frozen=True)
class ChatResourceContext:
    """Resource metadata needed by the model request and document execution pipeline."""

    history_files: list[dict]
    has_scoped_documents: bool
    has_chat_files: bool
    has_files: bool
    include_file_context: bool
    agent_enabled: bool


def resolve_chat_resource_context(
    *,
    messages: list[dict],
    request_metadata: dict,
    request_files: list[dict] | dict | None,
    resource_manifest: list[dict] | None,
    base_include_file_context: bool,
) -> ChatResourceContext:
    """Merge file references from the current request, history and authorized chat manifest."""
    manifest = resource_manifest or []
    has_scoped_documents = bool(manifest)
    user_message = request_metadata.get('user_message') or {}
    candidate_file_lists = [
        request_metadata.get('files'),
        request_files,
        user_message.get('files'),
    ]
    candidate_file_lists.extend(message.get('files') for message in messages if message.get('role') == 'user')

    file_refs = {}
    for attachments in candidate_file_lists:
        if isinstance(attachments, dict):
            attachments = [attachments]
        for item in attachments or []:
            if (
                isinstance(item, dict)
                and item.get('id')
                and item.get('type', 'file') == 'file'
                and not item.get('legacy')
            ):
                file_refs[str(item['id'])] = item
    for item in manifest:
        file_id = str(item['id'])
        file_refs[file_id] = {
            **file_refs.get(file_id, {}),
            **item,
        }

    history_files = list(file_refs.values())
    has_files = bool(
        has_scoped_documents
        or request_files
        or user_message.get('files')
        or any(message.get('files') for message in messages if message.get('role') == 'user')
    )
    include_file_context = base_include_file_context
    if resource_aware_context_enabled(history_files, has_chat_resources=has_scoped_documents):
        include_file_context = True

    return ChatResourceContext(
        history_files=history_files,
        has_scoped_documents=has_scoped_documents,
        has_chat_files=bool(history_files) or has_scoped_documents,
        has_files=has_files,
        include_file_context=include_file_context,
        agent_enabled=document_agent_mode(messages, has_files=has_files),
    )
