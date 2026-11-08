"""Resolve → prepare task → bounded read → completion policy.

The resource search port is injected. No database, FastAPI application, request,
provider client or socket emitter is imported by this application service.
"""

import json
import time

from ..agent_execution import (
    can_answer_task,
    initialize_task_state,
    reconcile_resource_coverage,
    record_tool_result,
)
from ..resource_aware import (
    bounded_file_sources,
    is_independent_greeting,
    plan_resource_context,
    requires_resource_evidence,
    resolve_resource_scope,
    resource_aware_context_enabled,
    resource_file_context_budget,
)
from .contracts import (
    CompletionDecision,
    ExecutionBudget,
    ResourceIndexFailed,
    ResourceIndexPending,
    ResourceSearch,
)


def _resolve_task_scope(state: dict, files: list[dict], prompt: str) -> tuple[bool, str, list[str], bool]:
    checkpoint_active = bool(
        state.get('status') in {'pending', 'partial', 'in_progress', 'blocked'}
        and (state.get('goal') or state.get('steps') or state.get('resource_scope_ids'))
    )
    task_prompt = state.get('goal') if checkpoint_active and state.get('goal') else prompt
    saved_scope = state.get('resource_scope_ids') if checkpoint_active else None
    if saved_scope is None:
        scope_ids, ambiguous_scope = resolve_resource_scope(task_prompt, files)
    else:
        available_ids = {str(item['id']) for item in files if isinstance(item, dict) and item.get('id')}
        scope_ids = [str(file_id) for file_id in saved_scope if str(file_id) in available_ids]
        ambiguous_scope = len(scope_ids) != len(saved_scope)
        if ambiguous_scope:
            state.update(status='blocked', reason='resource_scope_changed')
    return checkpoint_active, task_prompt, scope_ids, ambiguous_scope


def _record_resource_manifest(state: dict, metadata: dict, action: str) -> None:
    if action != 'discover' or not metadata['resource_execution_requested'] or metadata['resource_scope_ambiguous']:
        return

    manifest = []
    limit = max(0, min(8_000, int(metadata['resource_file_context_char_budget']) // 4))
    complete = True
    for item in metadata['resource_files']:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        entry = {
            'id': str(item['id']),
            'name': str(item.get('name') or item.get('filename') or item['id'])[:256],
        }
        if item.get('revision') is not None:
            entry['revision'] = item['revision']
        candidate = manifest + [entry]
        if len(candidate) > 100 or len(json.dumps(candidate, ensure_ascii=False)) > limit:
            complete = False
            break
        manifest = candidate
    metadata['resource_manifest_context'] = json.dumps(manifest, ensure_ascii=False)
    record_tool_result(
        state,
        'list_editable_documents',
        {'resources': manifest, 'coverage_complete': complete},
    )
    if not complete:
        state.update(status='partial', reason='resource_manifest_truncated')


class ResourceRequestPipeline:
    def __init__(self, search: ResourceSearch | None = None):
        self.search = search

    def resolve(self, metadata: dict, prompt: str, capabilities, *, pending_task_ids: list[str]):
        enabled = resource_aware_context_enabled(
            metadata.get('resource_history_files'),
            has_chat_resources=metadata.get('resource_chat_has_files', False),
        )
        metadata['resource_aware_files'] = enabled
        if not enabled:
            metadata.pop('resource_files', None)
            return
        metadata['resource_files'] = metadata.get('resource_history_files') or []
        state = metadata.setdefault('agent_task_state', initialize_task_state(prompt))
        state.setdefault('schema_version', 1)
        state.setdefault('budget', ExecutionBudget().as_dict())
        metadata.setdefault('_resource_task_started_at', time.monotonic())
        checkpoint_active, task_prompt, scope_ids, ambiguous_scope = _resolve_task_scope(
            state,
            metadata['resource_files'],
            prompt,
        )
        pending = bool(metadata.get('pending_task_id') or pending_task_ids or checkpoint_active)
        metadata['pending_resource_task_ids'] = pending_task_ids
        metadata['resource_task_prompt'] = task_prompt
        metadata['resource_greeting'] = is_independent_greeting(prompt, pending_task=False)
        current_versions = {}
        for item in metadata['resource_files']:
            if not isinstance(item, dict) or not item.get('id'):
                continue
            resource_id = str(item['id'])
            revision = item.get('revision')
            if (
                resource_id in scope_ids
                and isinstance(revision, int)
                and not isinstance(revision, bool)
                and revision > 0
            ):
                current_versions[resource_id] = revision
        reconcile_resource_coverage(
            state,
            scope_ids,
            current_versions,
            preserve=checkpoint_active,
        )
        metadata['resource_scope_ids'] = scope_ids
        metadata['resource_scope_versions'] = current_versions
        metadata['resource_scope_ambiguous'] = ambiguous_scope
        metadata['resource_context_mode'] = plan_resource_context(task_prompt, has_pending_task=pending)
        action = state.get('required_action', 'read')
        metadata['resource_model_tools'] = capabilities.supports_tools
        metadata['resource_file_context_char_budget'] = resource_file_context_budget(
            capabilities.context_window_tokens,
            capabilities.max_output_tokens,
        )
        metadata['resource_execution_requested'] = not metadata['resource_greeting'] and (
            action in {'display', 'discover'} or requires_resource_evidence(task_prompt, has_pending_task=pending)
        )
        metadata['resource_evidence_requested'] = (
            not metadata['resource_greeting']
            and action not in {'display', 'discover'}
            and requires_resource_evidence(task_prompt, has_pending_task=pending)
        )
        state['requires_complete_coverage'] = bool(
            state.get('requires_complete_coverage') or metadata['resource_context_mode'] == 'FULL'
        )
        if (
            ambiguous_scope
            and metadata['resource_execution_requested']
            and state.get('reason') != 'resource_scope_changed'
        ):
            state.update(status='pending', reason='ambiguous_resource_scope')
        _record_resource_manifest(state, metadata, action)

    @staticmethod
    def consume_model_call(metadata: dict) -> bool:
        state = metadata.get('agent_task_state')
        if not isinstance(state, dict):
            return True
        budget = ExecutionBudget(**(state.get('budget') or {}))
        if not budget.consume_model_call():
            state.update(status='partial', reason='model_call_or_time_budget_exhausted')
            return False
        state['budget'] = budget.as_dict()
        return True

    async def read(self, owner_id: str, metadata: dict, prompt: str) -> list[dict]:
        if not metadata.get('resource_evidence_requested') or self.search is None:
            return []
        state = metadata['agent_task_state']
        if metadata.get('resource_scope_ambiguous'):
            if state.get('reason') != 'resource_scope_changed':
                state.update(status='pending', reason='ambiguous_resource_scope')
            return []
        budget = ExecutionBudget(**state['budget'])
        if not budget.consume_read():
            state.update(status='partial', reason='execution_budget_exhausted')
            return []
        priority = [
            str(item['id'])
            for item in (metadata.get('user_message') or {}).get('files', [])
            if isinstance(item, dict) and item.get('type') == 'file' and item.get('id')
        ]
        limit = metadata['resource_file_context_char_budget']
        state['budget'] = budget.as_dict()
        try:
            query = metadata.get('resource_task_prompt') or prompt
            raw = await self.search(
                owner_id,
                metadata.get('resource_files') or [],
                metadata.get('chat_id'),
                query,
                max_chunks=12 if metadata.get('resource_model_tools') else 24,
                per_document=3 if metadata.get('resource_model_tools') else 6,
                full_context=metadata.get('resource_context_mode') == 'FULL',
                priority_file_ids=priority,
                max_total_chars=limit,
                bounded_initial_read=not metadata.get('resource_model_tools'),
                scope_file_ids=metadata.get('resource_scope_ids') or None,
            )
            sources = bounded_file_sources(
                raw,
                query,
                priority_file_ids=priority,
                max_total_chars=limit,
                full_if_fits=metadata.get('resource_context_mode') == 'FULL',
            )
        except ResourceIndexPending as exc:
            state.update(
                status='pending',
                reason='resource_index_pending',
                index_job_ids=list(exc.job_ids),
            )
            return []
        except ResourceIndexFailed as exc:
            state.update(
                status='blocked',
                reason='resource_index_failed',
                index_job_ids=list(exc.job_ids),
            )
            return []
        except Exception as exc:
            # Keep transport errors out of prompts and task checkpoints.
            state.update(status='blocked', reason=f'resource_read_failed:{type(exc).__name__}')
            return []
        if not sources:
            state.update(status='blocked', reason='resource_text_unavailable')
            return []
        record_tool_result(state, 'search_editable_document', sources)
        complete = all(
            meta.get('coverage_complete') is True and not meta.get('truncated')
            for source in sources
            for meta in source.get('metadata', [])
        )
        complete = complete and bool(sources) and state.get('coverage_complete') is True
        metadata['resource_full_context_supplied'] = complete and state['requires_complete_coverage']
        if state['requires_complete_coverage'] and not complete:
            metadata['resource_context_mode'] = 'HIERARCHICAL'
        return sources

    @staticmethod
    def decision(metadata: dict) -> CompletionDecision:
        if not metadata.get('resource_aware_files') or not (
            metadata.get('resource_execution_requested', metadata.get('resource_evidence_requested'))
        ):
            return CompletionDecision('completed')
        state = metadata.get('agent_task_state') or {}
        complete = state.get('coverage_complete', False)
        if state.get('status') in {'blocked', 'pending', 'cancelled'}:
            return CompletionDecision(state['status'], state.get('reason', 'resource_action_required'), complete)
        if can_answer_task(state) and (not state.get('requires_complete_coverage') or complete):
            return CompletionDecision('completed', coverage_complete=complete)
        return CompletionDecision('partial', state.get('reason', 'additional_resource_evidence_required'), complete)
