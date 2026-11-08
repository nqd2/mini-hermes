"""Select legacy or Resource API document context for one chat request."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceContextAdapters:
    """Open WebUI and Mini-Hermes services used to prepare resource context."""

    chat_completion_files_handler: Callable[..., Awaitable[Any]]
    resource_aware_file_ids: Callable[..., list[str]]
    has_resource_file_tools: Callable[..., bool]
    read_resource_evidence: Callable[..., Awaitable[list[dict]]]


async def prepare_resource_context(request, form_data, extra_params, user, model, metadata, prompt, sources, adapters):
    """Retrieve bounded or legacy context and update the request's available evidence."""
    model_capabilities = model.get('info', {}).get('meta', {}).get('capabilities') or {}
    file_context_enabled = not metadata.get('resource_aware_files') and model_capabilities.get('file_context', True)

    if file_context_enabled:
        form_data, retrieved_sources = await _retrieve_legacy_context(request, form_data, extra_params, user, adapters)
        sources.extend(retrieved_sources)
    elif metadata.get('resource_aware_files'):
        sources.extend(await _retrieve_attached_knowledge(request, form_data, extra_params, user, metadata, adapters))

    if metadata.get('resource_aware_files'):
        resource_model_tools = metadata.get('resource_model_tools')
        metadata['resource_file_tools_available'] = bool(
            resource_model_tools and adapters.has_resource_file_tools(metadata.get('tools'))
        )
        sources.extend(await adapters.read_resource_evidence(user.id, metadata, prompt or ''))
        if not resource_model_tools:
            form_data.pop('tools', None)
            form_data.pop('tool_choice', None)

    return form_data, metadata, sources


async def _retrieve_legacy_context(request, form_data, extra_params, user, adapters):
    try:
        updated_form, flags = await adapters.chat_completion_files_handler(request, form_data, extra_params, user)
        return updated_form, flags.get('sources', [])
    except Exception as e:
        log.exception(e)
        return form_data, []


async def _retrieve_attached_knowledge(request, form_data, extra_params, user, metadata, adapters):
    # Keep model-attached collections in the legacy search path while excluding
    # chat-uploaded file references managed by Resource API.
    resource_ids = set(adapters.resource_aware_file_ids(metadata.get('resource_files')))
    knowledge_files = [
        item
        for item in metadata.get('files') or []
        if not (
            isinstance(item, dict)
            and item.get('type', 'file') == 'file'
            and str(item.get('id') or '') in resource_ids
        )
    ]
    if not knowledge_files:
        return []

    knowledge_form = dict(form_data)
    knowledge_metadata = dict(metadata)
    knowledge_metadata['files'] = knowledge_files
    knowledge_form['metadata'] = knowledge_metadata
    try:
        _, flags = await adapters.chat_completion_files_handler(request, knowledge_form, extra_params, user)
        return flags.get('sources', [])
    except Exception as e:
        log.exception('Attached knowledge retrieval failed: %s', e)
        return []
