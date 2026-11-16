"""Tests for live resource task progress and backend-driven auto-resume."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.mini_hermes import resource_wait_watcher
from open_webui.utils.chat_runtime.resource_execution import ResourceAnswerGate, prepare_resource_tool_result


def test_answer_gate_pauses_without_continue_response_prose_in_saved_chat():
    metadata = {
        'chat_id': 'chat-1',
        'message_id': 'msg-1',
        'resource_aware_files': ['upload-1'],
        'resource_execution_requested': True,
        'resource_summary_task_required': True,
        'resource_summary_task_phase': 'start',
        'resource_scope_ids': ['upload-1'],
        'resource_scope_versions': {'upload-1': 1},
        'agent_task_state': {
            'schema_version': 1,
            'required_action': 'read',
            'required_actions': ['read'],
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'resource_scope_ids': ['upload-1'],
            'resource_scope_versions': {'upload-1': 1},
            'covered_resource_versions': {},
            'status': 'in_progress',
            'steps': [],
            'evidence_ids': [],
            'resource_task_ids': [],
            'resource_index_job_ids': [],
        },
    }
    prepare_resource_tool_result(metadata, 'start_document_summary_task', {'task_id': 'task-1', 'status': 'pending'})
    output = [{'type': 'function_call', 'name': 'start_document_summary_task', 'call_id': 'c1'}]
    visible = ResourceAnswerGate(metadata, enabled=True).present(output, final=True)

    assert metadata['resource_pending_wait'] == {
        'reason': 'resource_summary_task_pending',
        'task_ids': ['task-1'],
        'index_job_ids': [],
    }
    assert len(visible) == 1
    assert visible[0]['type'] == 'function_call'


def test_answer_gate_pauses_for_index_pending_in_saved_chat():
    metadata = {
        'chat_id': 'chat-1',
        'message_id': 'msg-1',
        'resource_aware_files': ['upload-1'],
        'resource_execution_requested': True,
        'resource_summary_task_required': True,
        'resource_summary_task_phase': 'start',
        'resource_scope_ids': ['upload-1'],
        'resource_scope_versions': {'upload-1': 1},
        'agent_task_state': {
            'schema_version': 1,
            'required_action': 'read',
            'required_actions': ['read'],
            'requires_complete_coverage': True,
            'coverage_complete': False,
            'resource_scope_ids': ['upload-1'],
            'resource_scope_versions': {'upload-1': 1},
            'covered_resource_versions': {},
            'status': 'in_progress',
            'steps': [],
            'evidence_ids': [],
            'resource_task_ids': [],
            'resource_index_job_ids': [],
        },
    }
    prepare_resource_tool_result(
        metadata,
        'start_document_summary_task',
        {'status': 'pending', 'index_job_ids': ['job-1']},
    )
    visible = ResourceAnswerGate(metadata, enabled=True).present([], final=True)

    assert metadata['resource_pending_wait'] == {
        'reason': 'resource_index_pending',
        'task_ids': [],
        'index_job_ids': ['job-1'],
    }
    assert visible == []


@pytest.mark.asyncio
async def test_watcher_emits_progress_and_resumes_turn_on_completion(monkeypatch):
    snapshots = iter(
        [
            {
                'schema_version': 1,
                'kind': 'summary',
                'status': 'processing',
                'task_id': 'task-1',
                'index_job_id': None,
                'phase': 'chunks',
                'progress': {'completed_units': 52, 'total_units': 152},
                'error': None,
            },
            {
                'schema_version': 1,
                'kind': 'summary',
                'status': 'completed',
                'task_id': 'task-1',
                'index_job_id': None,
                'phase': 'reduce',
                'progress': {'completed_units': 152, 'total_units': 152},
                'error': None,
            },
        ]
    )
    emitted = []
    resumed = []

    async def fake_fetch(pending_wait, owner_id):
        return next(snapshots)

    async def fake_emit(**kwargs):
        emitted.append(kwargs)

    async def fake_resume(app, *, user_id, chat_id, message_id):
        resumed.append((user_id, chat_id, message_id))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(resource_wait_watcher, 'fetch_resource_progress', fake_fetch)
    monkeypatch.setattr(resource_wait_watcher, '_emit_progress_update', fake_emit)
    monkeypatch.setattr(resource_wait_watcher, 'resume_pending_resource_turn', fake_resume)
    monkeypatch.setattr(resource_wait_watcher.asyncio, 'sleep', no_sleep)

    await resource_wait_watcher._watch_resource_wait(
        SimpleNamespace(state=SimpleNamespace()),
        chat_id='chat-1',
        message_id='msg-1',
        owner_id='user-1',
        session_id='sess-1',
        pending_wait={'reason': 'resource_summary_task_pending', 'task_ids': ['task-1'], 'index_job_ids': []},
    )

    assert len(emitted) == 2
    assert emitted[0]['done'] is False
    assert emitted[0]['receipt']['progress'] == {'completed_units': 52, 'total_units': 152}
    assert emitted[1]['receipt']['status'] == 'completed'
    assert resumed == [('user-1', 'chat-1', 'msg-1')]


@pytest.mark.asyncio
async def test_watcher_marks_done_without_resume_on_failure(monkeypatch):
    emitted = []
    resumed = []

    monkeypatch.setattr(
        resource_wait_watcher,
        'fetch_resource_progress',
        AsyncMock(
            return_value={
                'schema_version': 1,
                'kind': 'summary',
                'status': 'failed',
                'task_id': 'task-1',
                'index_job_id': None,
                'phase': 'chunks',
                'progress': {'completed_units': 10, 'total_units': 152},
                'error': 'Provider error',
            }
        ),
    )

    async def fake_emit(**kwargs):
        emitted.append(kwargs)

    async def fake_resume(*args, **kwargs):
        resumed.append(True)

    monkeypatch.setattr(resource_wait_watcher, '_emit_progress_update', fake_emit)
    monkeypatch.setattr(resource_wait_watcher, 'resume_pending_resource_turn', fake_resume)

    await resource_wait_watcher._watch_resource_wait(
        SimpleNamespace(state=SimpleNamespace()),
        chat_id='chat-1',
        message_id='msg-1',
        owner_id='user-1',
        session_id='sess-1',
        pending_wait={'reason': 'resource_summary_task_pending', 'task_ids': ['task-1'], 'index_job_ids': []},
    )

    assert len(emitted) == 1
    assert emitted[0]['done'] is True
    assert emitted[0]['receipt']['error'] == 'Provider error'
    assert resumed == []
