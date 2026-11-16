"""Background watcher for pending document index jobs and summary tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from starlette.requests import Request

log = logging.getLogger(__name__)

WATCHER_POLL_INTERVAL_SECONDS = 1.5


def build_initial_progress_receipt(pending_wait: dict[str, Any]) -> dict[str, Any]:
    """Build the initial UI progress receipt for a paused resource wait."""
    is_index = pending_wait.get('reason') == 'resource_index_pending'
    task_id = next((str(tid) for tid in (pending_wait.get('task_ids') or []) if tid), None)
    index_job_id = next((str(jid) for jid in (pending_wait.get('index_job_ids') or []) if jid), None)
    return {
        'schema_version': 1,
        'kind': 'index' if is_index else 'summary',
        'status': 'pending',
        'task_id': task_id,
        'index_job_id': index_job_id,
        'phase': 'indexing' if is_index else 'chunks',
        'progress': {'completed_units': 0, 'total_units': 0},
        'error': None,
    }


async def fetch_resource_progress(pending_wait: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Read current progress for the watched summary task or index job."""
    from open_webui.internal.db import get_async_db
    from .models import Document, ResourceIndexJob
    from .resource_tasks import get_summary_task

    receipt = build_initial_progress_receipt(pending_wait)
    async with get_async_db() as db:
        if receipt['kind'] == 'index' and receipt['index_job_id']:
            job = (
                await db.execute(select(ResourceIndexJob).where(ResourceIndexJob.id == receipt['index_job_id']))
            ).scalar_one_or_none()
            if job is None:
                receipt['status'] = 'failed'
                receipt['error'] = 'Index job not found.'
                return receipt
            doc = (await db.execute(select(Document).where(Document.id == job.document_id))).scalar_one_or_none()
            if doc is not None:
                receipt['filename'] = doc.filename
            status = 'completed' if job.status == 'ready' else job.status
            receipt['status'] = status
            receipt['phase'] = 'indexing'
            receipt['progress'] = {
                'completed_units': 1 if status == 'completed' else 0,
                'total_units': 1,
            }
            receipt['error'] = job.error
            return receipt

        if receipt['task_id']:
            task = await get_summary_task(db, receipt['task_id'], owner_id, evidence_offset=0, evidence_limit=1)
            doc = (
                await db.execute(select(Document).where(Document.id == task['document_id']))
            ).scalar_one_or_none()
            if doc is not None:
                receipt['filename'] = doc.filename
            receipt['status'] = task['status']
            receipt['phase'] = task.get('phase') or 'chunks'
            receipt['progress'] = task.get('progress') or {'completed_units': 0, 'total_units': 0}
            receipt['error'] = task.get('error')
            return receipt

    receipt['status'] = 'failed'
    receipt['error'] = 'No resource task or index job ID to watch.'
    return receipt


async def _emit_progress_update(
    *,
    chat_id: str,
    message_id: str,
    owner_id: str,
    session_id: str | None,
    receipt: dict[str, Any],
    done: bool,
) -> None:
    from open_webui.models.chats import Chats
    from open_webui.socket.main import get_event_emitter

    await Chats.upsert_message_to_chat_by_id_and_message_id(
        chat_id,
        message_id,
        {'done': done, 'meta': {'resource_task_progress': receipt}},
        touch=False,
    )
    emitter = await get_event_emitter(
        {'user_id': owner_id, 'chat_id': chat_id, 'message_id': message_id, 'session_id': session_id},
        update_db=False,
    )
    if emitter:
        await emitter(
            {
                'type': 'chat:completion',
                'data': {'done': done, 'meta': {'resource_task_progress': receipt}},
            }
        )
        await emitter({'type': 'chat:active', 'data': {'active': not done}})


async def resume_pending_resource_turn(
    app: Any,
    *,
    user_id: str,
    chat_id: str,
    message_id: str,
) -> None:
    """Resume a paused chat turn after its underlying resource task or index finishes."""
    from open_webui.models.chats import Chats
    from open_webui.models.users import Users
    from open_webui.utils.tool_approval import build_tool_approval_resume_payload

    handler = getattr(app.state, 'CHAT_COMPLETION_HANDLER', None)
    if handler is None:
        log.warning('CHAT_COMPLETION_HANDLER unavailable; cannot auto-resume %s/%s', chat_id, message_id)
        return

    user = await Users.get_user_by_id(user_id)
    chat = await Chats.get_chat_by_id(chat_id)
    if user is None or chat is None:
        return

    payload = await build_tool_approval_resume_payload(chat_id, message_id, chat=chat)
    internal_request = Request(
        {
            'type': 'http',
            'app': app,
            'headers': [],
            'method': 'POST',
            'path': '/api/chat/completions',
            'query_string': b'',
        }
    )
    internal_request.state.tool_approval_resume = (chat_id, message_id, 'model')
    try:
        await handler(internal_request, payload, user)
    finally:
        if hasattr(internal_request.state, 'tool_approval_resume'):
            del internal_request.state.tool_approval_resume


async def _watch_resource_wait(
    app: Any,
    *,
    chat_id: str,
    message_id: str,
    owner_id: str,
    session_id: str | None,
    pending_wait: dict[str, Any],
) -> None:
    last_signature: tuple[Any, ...] | None = None
    try:
        while True:
            receipt = await fetch_resource_progress(pending_wait, owner_id)
            progress = receipt.get('progress') or {}
            signature = (
                receipt.get('status'),
                receipt.get('phase'),
                progress.get('completed_units'),
                progress.get('total_units'),
                receipt.get('error'),
            )
            status = receipt.get('status')
            if status == 'completed':
                await _emit_progress_update(
                    chat_id=chat_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    session_id=session_id,
                    receipt=receipt,
                    done=False,
                )
                await resume_pending_resource_turn(
                    app,
                    user_id=owner_id,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return
            if status in {'failed', 'cancelled'}:
                await _emit_progress_update(
                    chat_id=chat_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    session_id=session_id,
                    receipt=receipt,
                    done=True,
                )
                return
            if signature != last_signature:
                last_signature = signature
                await _emit_progress_update(
                    chat_id=chat_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    session_id=session_id,
                    receipt=receipt,
                    done=False,
                )
            await asyncio.sleep(WATCHER_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Resource wait watcher failed for %s/%s', chat_id, message_id)


def schedule_resource_wait_watcher(
    app: Any,
    *,
    chat_id: str,
    message_id: str,
    owner_id: str,
    session_id: str | None,
    pending_wait: dict[str, Any],
) -> asyncio.Task:
    """Start (or replace) the background watcher for a paused resource wait."""
    watchers: dict[tuple[str, str], asyncio.Task] = getattr(app.state, '_resource_wait_watchers', None)
    if watchers is None:
        watchers = {}
        app.state._resource_wait_watchers = watchers

    key = (chat_id, message_id)
    existing = watchers.get(key)
    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(
        _watch_resource_wait(
            app,
            chat_id=chat_id,
            message_id=message_id,
            owner_id=owner_id,
            session_id=session_id,
            pending_wait=pending_wait,
        )
    )
    watchers[key] = task
    task.add_done_callback(lambda t: watchers.pop(key, None) if watchers.get(key) is t else None)
    return task
