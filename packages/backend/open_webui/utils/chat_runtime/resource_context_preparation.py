"""Select legacy or Resource API document context for one chat request."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


def _function_tool_choice(name: str) -> dict[str, Any]:
    return {'type': 'function', 'function': {'name': name}}


def _provider_tool_names(form_data: dict) -> set[str]:
    return {
        tool.get('function', {}).get('name')
        for tool in form_data.get('tools') or []
        if isinstance(tool, dict) and isinstance(tool.get('function'), dict)
    }


def _require_document_summary_tool(form_data: dict, metadata: dict) -> None:
    """Route incomplete, whole-document requests into the durable task flow."""
    state = metadata.get('agent_task_state') or {}
    prior_task_ids = state.get('resource_task_ids') or metadata.get('pending_resource_task_ids') or []
    if not (
        metadata.get('resource_execution_requested')
        and metadata.get('resource_context_mode') in {'FULL', 'HIERARCHICAL'}
        and state.get('requires_complete_coverage') is True
        and state.get('coverage_complete') is not True
        and state.get('status') not in {'blocked', 'cancelled'}
        and (
            state.get('status') != 'pending'
            or bool(prior_task_ids)
            or state.get('reason') == 'resource_index_pending'
        )
        and metadata.get('resource_file_tools_available')
        and (metadata.get('params') or {}).get('function_calling') != 'legacy'
        and len(metadata.get('resource_scope_ids') or []) == 1
    ):
        return

    tool_names = _provider_tool_names(form_data)
    if not {'list_editable_documents', 'start_document_summary_task', 'get_document_task'} <= tool_names:
        return

    task_ids = list(dict.fromkeys(
        str(task_id)
        for task_id in [*(state.get('resource_task_ids') or []), *(metadata.get('pending_resource_task_ids') or [])]
        if isinstance(task_id, str) and task_id
    ))
    if task_ids:
        metadata['resource_summary_task_required'] = True
        metadata['resource_summary_task_phase'] = 'status'
        form_data['tool_choice'] = _function_tool_choice('get_document_task')
    else:
        metadata['resource_summary_task_required'] = True
        metadata['resource_summary_task_phase'] = 'list'
        form_data['tool_choice'] = _function_tool_choice('list_editable_documents')


def apply_resource_summary_tool_choice(form_data: dict, metadata: dict) -> None:
    """Apply the next required document-task step after a tool result."""
    phase_to_tool = {
        'list': 'list_editable_documents',
        'start': 'start_document_summary_task',
        'status': 'get_document_task',
    }
    phase = metadata.get('resource_summary_task_phase')
    tool_name = phase_to_tool.get(phase)
    if tool_name and tool_name in _provider_tool_names(form_data):
        form_data['tool_choice'] = _function_tool_choice(tool_name)
    elif metadata.get('resource_summary_task_required'):
        form_data['tool_choice'] = 'none' if phase in {'awaiting', 'awaiting_index'} else 'auto'


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
        else:
            _require_document_summary_tool(form_data, metadata)

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
