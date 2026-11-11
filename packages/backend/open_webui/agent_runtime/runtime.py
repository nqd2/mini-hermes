"""Canonical coordinator for durable chat turns and tool execution."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from open_webui.utils.json_codec import JSONCodec

from .artifacts import AgentArtifactStore
from .contracts import CURRENT_ENGINE_TYPE, CURRENT_ENGINE_VERSION, ConversationEngineBinding, RunBinding
from .outputs import ToolOutputManager
from .repository import AgentRepository
from .request_budget import RequestBudgetManager

log = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    'calculate_timestamp',
    'get_current_timestamp',
    'grep_chat_files',
    'grep_knowledge_files',
    'grep_skill_files',
    'list_chat_files',
    'list_knowledge',
    'list_knowledge_bases',
    'list_memory_paths',
    'list_memories',
    'list_automations',
    'query_chat_files',
    'query_knowledge_bases',
    'query_knowledge_files',
    'read_agent_artifact',
    'read_memory_path',
    'read_skill_file',
    'search_calendar_events',
    'search_channel_messages',
    'search_channels',
    'search_chats',
    'search_knowledge_bases',
    'search_knowledge_files',
    'search_memories',
    'search_notes',
    'search_tools',
    'view_channel_message',
    'view_channel_thread',
    'view_chat',
    'view_file',
    'view_knowledge_file',
    'view_note',
    'view_skill',
}
_SECRET_KEYS = {
    'api_key',
    'x_api_key',
    'authorization',
    'access_token',
    'refresh_token',
    'secret',
    'password',
    'cookie',
    'set_cookie',
}
_configured_runtime: AgentRuntime | None = None


class AgentRuntime:
    def __init__(
        self,
        repository: AgentRepository,
        *,
        artifact_store: AgentArtifactStore | None = None,
        output_manager: ToolOutputManager | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 90,
    ):
        self.repository = repository
        self.engine_binding = ConversationEngineBinding(CURRENT_ENGINE_TYPE, CURRENT_ENGINE_VERSION)
        self.artifact_store = artifact_store
        self.output_manager = output_manager or ToolOutputManager(_configured_tool_output_limit())
        self.worker_id = worker_id or f'chat-{uuid.uuid4()}'
        self.lease_seconds = max(15, lease_seconds)

    @classmethod
    def configured(cls) -> AgentRuntime:
        global _configured_runtime
        if _configured_runtime is not None:
            return _configured_runtime
        from open_webui.internal.db import AsyncSessionLocal

        repository = AgentRepository(AsyncSessionLocal)
        _configured_runtime = cls(repository, artifact_store=AgentArtifactStore.configured())
        return _configured_runtime

    async def start(self, **kwargs: Any) -> Any:
        """Create or idempotently return the run owned by this execution engine."""
        return await self.run_chat_turn(**kwargs)

    async def stream(self, turn_id: str, *, owner_id: str, after_sequence: int = 0, limit: int = 200) -> dict:
        status = await self.repository.get_turn_status(turn_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail='Agent turn not found.')
        page = await self.repository.get_event_page(turn_id, after_sequence=after_sequence, limit=limit)
        if page['snapshot_required']:
            page['snapshot'] = status
        return page

    async def get_status(self, turn_id: str, *, owner_id: str) -> dict:
        status = await self.repository.get_turn_status(turn_id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail='Agent turn not found.')
        return status

    async def cancel(self, turn_id: str, *, owner_id: str) -> dict:
        turn = await self.repository.request_cancel_turn(turn_id, owner_id=owner_id)
        if turn.status == 'cancelled':
            session = await self.repository.get_turn_session(turn.session_id)
            if session:
                await self._update_task_status(session.id, owner_id, turn.id, 'cancelled')
        status = await self.repository.get_turn_status(turn_id, owner_id=owner_id)
        return status or {'turn_id': turn.id, 'status': turn.status}

    async def _existing_run_status(self, turn, *, owner_id: str, metadata: dict) -> dict:
        """Return the durable receipt for an idempotent start without rerunning effects."""
        status = await self.repository.get_turn_status(turn.id, owner_id=owner_id)
        if status is None:
            raise HTTPException(status_code=404, detail='Agent turn not found.')
        binding = status['run_binding']
        metadata['agent_turn_id'] = turn.id
        metadata['agent_session_id'] = turn.session_id
        metadata['agent_run_id'] = binding['engine_run_id']
        metadata['agent_thread_id'] = binding['engine_thread_id']
        metadata['agent_run_binding'] = binding
        return {**status, 'idempotent_replay': True}

    async def resume(
        self,
        turn_id: str,
        *,
        owner_id: str,
        handler: Callable[[], Awaitable[Any]],
        request_payload: dict | None = None,
        metadata: dict | None = None,
    ) -> Any:
        """Resume one pinned run; caller rebuilds its request adapter around the stored run."""
        turn = await self.repository.get_owned_turn(turn_id, owner_id=owner_id)
        if turn is None:
            raise HTTPException(status_code=404, detail='Agent turn not found.')
        session = await self.repository.get_turn_session(turn.session_id)
        if session is None or ConversationEngineBinding.from_session(session) != self.engine_binding:
            raise HTTPException(status_code=409, detail='Run is bound to another execution engine.')
        if turn.status not in {'waiting_approval', 'interrupted', 'paused'} or turn.cancel_requested:
            raise HTTPException(status_code=409, detail='Agent turn cannot be resumed in its current state.')
        if await self.repository.has_unknown_tool_outcomes(turn.id):
            raise HTTPException(status_code=409, detail='An unknown external tool outcome blocks resume.')
        if not turn.chat_message_id:
            raise HTTPException(status_code=409, detail='Run has no resumable chat message identity.')
        return await self.run_chat_turn(
            owner_id=owner_id,
            chat_id=session.chat_id,
            chat_message_id=turn.chat_message_id,
            user_message_id=turn.user_message_id,
            model_id=turn.model_id,
            request_payload=request_payload if request_payload is not None else turn.request_payload,
            metadata=metadata if metadata is not None else {},
            parent_session_id=session.parent_session_id,
            resume_turn_id=turn.id,
            handler=handler,
        )

    async def run_chat_turn(
        self,
        *,
        owner_id: str,
        chat_id: str | None,
        chat_message_id: str | None,
        user_message_id: str | None,
        model_id: str | None,
        request_payload: dict,
        metadata: dict,
        handler: Callable[[], Awaitable[Any]],
        parent_session_id: str | None = None,
        resume_turn_id: str | None = None,
    ) -> Any:
        safe_payload = _json_safe(request_payload)
        session = await self.repository.get_session_for_chat(chat_id, owner_id=owner_id) if chat_id else None
        if session is None:
            session = await self.repository.create_session(
                owner_id,
                chat_id,
                parent_session_id=parent_session_id,
                task_state=_initial_task_state(safe_payload, user_message_id),
                engine_binding=self.engine_binding,
            )
        elif ConversationEngineBinding.from_session(session) != self.engine_binding:
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'conversation_engine_mismatch',
                    'message': (
                        'This conversation is pinned to another execution engine and cannot be switched between turns.'
                    ),
                },
            )

        approval_resume_requested = bool(metadata.get('tool_approval_resume'))
        resume_requested = resume_turn_id is not None or approval_resume_requested
        turn = None
        if resume_turn_id:
            turn = await self.repository.get_owned_turn(resume_turn_id, owner_id=owner_id)
            if turn is None or turn.session_id != session.id or turn.chat_message_id != chat_message_id:
                raise HTTPException(status_code=404, detail='Agent turn not found.')
            if turn.status not in {'waiting_approval', 'interrupted', 'paused'}:
                raise HTTPException(status_code=409, detail='Agent turn cannot be resumed in its current state.')
        elif approval_resume_requested and chat_message_id:
            turn = await self.repository.find_resumable_turn(session.id, chat_message_id)
            if turn is None:
                raise HTTPException(status_code=409, detail='No resumable run exists for this approval.')
        if turn is None:
            turn = await self.repository.create_turn(
                session.id,
                safe_payload,
                model_id=model_id,
                chat_message_id=chat_message_id,
                user_message_id=user_message_id,
                idempotency_key=_chat_start_idempotency_key(chat_message_id, user_message_id),
            )
        if turn.status in {'waiting_approval', 'paused'} and not resume_requested:
            return await self._existing_run_status(turn, owner_id=owner_id, metadata=metadata)
        try:
            claimed = await self.repository.claim_turn_by_id(
                turn.id, self.worker_id, lease_seconds=self.lease_seconds
            )
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail == 'Agent turn cannot be claimed in its current state.':
                return await self._existing_run_status(turn, owner_id=owner_id, metadata=metadata)
            raise
        run_binding = RunBinding.from_records(session, claimed)
        metadata['agent_turn_id'] = claimed.id
        metadata['agent_session_id'] = session.id
        metadata['agent_run_id'] = run_binding.engine_run_id
        metadata['agent_thread_id'] = run_binding.engine_thread_id
        metadata['agent_run_binding'] = run_binding.as_dict()
        checkpoint = await self.repository.save_checkpoint(
            claimed.id,
            {
                'schema_version': 1,
                'stage': 'request_prepared',
                'message_id': chat_message_id,
                'user_message_id': user_message_id,
                'request_payload': safe_payload,
                'run_binding': run_binding.as_dict(),
            },
            kind='request',
        )
        await self.repository.update_session_turn_status(
            session.id,
            owner_id=owner_id,
            turn_id=claimed.id,
            status='in_progress',
            objective=_latest_user_text(safe_payload),
            user_message_id=user_message_id,
        )

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat(claimed.id, heartbeat_stop))
        try:
            result = await self._run_handler_until_cancelled(handler, claimed.id)
            if metadata.get('agent_error'):
                await self._fail_turn(claimed.id, metadata['agent_error'])
                current = await self.repository.get_turn(claimed.id)
                await self._update_task_status(
                    session.id, owner_id, claimed.id, current.status if current else 'failed'
                )
            elif isinstance(result, Response) and result.status_code >= 400:
                await self._fail_turn(
                    claimed.id,
                    {'code': 'chat_response_error', 'status_code': result.status_code},
                )
                current = await self.repository.get_turn(claimed.id)
                await self._update_task_status(
                    session.id, owner_id, claimed.id, current.status if current else 'failed'
                )
            elif isinstance(result, StreamingResponse):
                return self._track_stream(result, claimed.id, owner_id, session.id, heartbeat_stop, heartbeat_task)
            elif isinstance(result, dict) and result.get('paused'):
                await self.repository.set_turn_status(
                    claimed.id,
                    'waiting_approval',
                    event_type='turn.waiting_approval',
                    payload={'turn_id': claimed.id, 'message_id': chat_message_id},
                )
                await self._update_task_status(session.id, owner_id, claimed.id, 'waiting_approval')
            elif await self.repository.has_unresolved_tool_calls(claimed.id):
                await self.repository.set_turn_status(
                    claimed.id,
                    'waiting_approval',
                    event_type='turn.waiting_approval',
                    payload={'turn_id': claimed.id, 'message_id': chat_message_id},
                )
                await self._update_task_status(session.id, owner_id, claimed.id, 'waiting_approval')
            else:
                await self.repository.complete_turn(
                    claimed.id,
                    result={'message_id': chat_message_id, 'checkpoint_version': checkpoint.version},
                )
                await self._update_task_status(session.id, owner_id, claimed.id, 'completed')
            return result
        except asyncio.CancelledError:
            unknown = await self.repository.interrupt_running_tool_calls(claimed.id)
            cancel_requested = await self.repository.is_cancel_requested(claimed.id)
            final_status = 'paused' if unknown else 'cancelled' if cancel_requested else 'interrupted'
            await self.repository.set_turn_status(
                claimed.id,
                final_status,
                event_type='turn.recovery_required' if unknown else 'turn.cancelled' if cancel_requested else 'turn.interrupted',
                payload={'turn_id': claimed.id, 'reason': 'request_cancelled'},
            )
            await self._update_task_status(session.id, owner_id, claimed.id, final_status)
            raise
        except BaseException as error:
            await self._fail_turn(claimed.id, _error_payload(error))
            current = await self.repository.get_turn(claimed.id)
            await self._update_task_status(session.id, owner_id, claimed.id, current.status if current else 'failed')
            raise
        finally:
            if not isinstance(locals().get('result'), StreamingResponse):
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def begin_tool_call(
        self,
        turn_id: str,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict,
        tool: dict,
    ) -> tuple[Any, bool]:
        retry_safe = _is_retry_safe_builtin(tool_name, tool)
        invocation, acquired = await self.repository.begin_tool_call(
            turn_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments=_json_safe(arguments),
            retry_safe=retry_safe,
            idempotency_key=f'{turn_id}:{call_id}',
        )
        if not acquired:
            if invocation.status == 'succeeded':
                return invocation, False
            raise HTTPException(
                status_code=409,
                detail=(
                    'Tool invocation is already running or its external outcome is unknown; '
                    'it was not executed a second time.'
                ),
            )
        return invocation, True

    async def record_tool_approval(
        self,
        turn_id: str,
        *,
        owner_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict,
        decision: str,
        approved_by: str,
        tool: dict | None = None,
    ) -> Any:
        return await self.repository.record_tool_approval(
            turn_id,
            owner_id=owner_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments=_json_safe(arguments),
            decision=decision,
            approved_by=approved_by,
            retry_safe=_is_retry_safe_builtin(tool_name, tool or {}),
            idempotency_key=f'{turn_id}:{call_id}',
        )

    def wrap_event_emitter(
        self,
        turn_id: str,
        emitter: Callable[[dict], Awaitable[Any]] | None,
        *,
        message_id: str | None = None,
    ) -> Callable[[dict], Awaitable[Any]] | None:
        if emitter is None:
            return None
        if getattr(emitter, '_agent_runtime_turn_id', None) == turn_id:
            return emitter

        async def durable_emitter(event_data: dict) -> Any:
            if not _should_persist_client_event(event_data):
                forwarded = copy.deepcopy(event_data)
                if isinstance(forwarded, dict):
                    forwarded['runtime_turn_id'] = turn_id
                    data = forwarded.get('data')
                    if isinstance(data, dict):
                        data['runtime_turn_id'] = turn_id
                return await emitter(forwarded)

            event_type = str(event_data.get('type') or 'unknown')[:80]
            sanitized = _json_safe(event_data)
            serialized = JSONCodec.dumps(sanitized, ensure_ascii=False)
            payload = {
                'message_id': message_id,
                'event': sanitized,
            }
            serialized_payload = JSONCodec.dumps(payload, ensure_ascii=False)
            if len(serialized_payload) > 32_000 or len(serialized_payload.encode('utf-8')) > 64_000:
                payload = {
                    'message_id': message_id,
                    'event_type': event_type,
                    'snapshot_required': True,
                    'original_chars': len(serialized),
                    'original_bytes': len(serialized.encode('utf-8')),
                }

            record = await self.repository.append_event(turn_id, f'client.{event_type}', payload)
            forwarded = copy.deepcopy(event_data)
            if isinstance(forwarded, dict):
                forwarded['runtime_turn_id'] = turn_id
                forwarded['runtime_event_id'] = record.id
                forwarded['runtime_event_sequence'] = record.sequence
                data = forwarded.get('data')
                if isinstance(data, dict):
                    data['runtime_turn_id'] = turn_id
                    data['runtime_event_id'] = record.id
                    data['runtime_event_sequence'] = record.sequence
            result = await emitter(forwarded)
            await self.repository.mark_event_published(record.id)
            return result

        durable_emitter._agent_runtime_turn_id = turn_id
        return durable_emitter

    async def complete_tool_call(
        self,
        turn_id: str,
        call_id: str,
        result: Any,
        *,
        owner_id: str,
    ) -> Any:
        turn = await self.repository.get_turn(turn_id)
        if turn is None:
            raise HTTPException(status_code=404, detail='Agent turn not found.')
        invocation = await self.repository.get_tool_call(turn_id, call_id)
        output_limits = await self._remaining_tool_output_budget(turn_id)
        try:
            bounded = await self.output_manager.process(
                _json_safe(result),
                model=turn.model_id or '',
                artifacts=self.artifact_store,
                owner_id=owner_id,
                session_id=turn.session_id,
                turn_id=turn.id,
                **output_limits,
            )
        except BaseException as error:
            if invocation and invocation.retry_safe:
                await self.repository.release_tool_call_for_retry(
                    turn_id,
                    call_id,
                    {'code': 'tool_output_persistence_failed', 'message': type(error).__name__},
                )
            elif invocation:
                await self.repository.fail_tool_call(
                    turn_id,
                    call_id,
                    {'code': 'tool_output_persistence_failed', 'message': type(error).__name__},
                    uncertain=True,
                )
            raise
        await self.repository.complete_tool_call(
            turn_id,
            call_id,
            _json_safe(bounded.value),
            result_artifact_id=bounded.artifact_id,
        )
        return bounded.value

    async def _remaining_tool_output_budget(self, turn_id: str) -> dict[str, int]:
        checkpoint = await self.repository.latest_checkpoint(turn_id, kind='context')
        if checkpoint is None or not isinstance(checkpoint.data, dict):
            return {}
        metrics = checkpoint.data.get('metrics')
        if not isinstance(metrics, dict):
            return {}

        limits = RequestBudgetManager().limits
        output_limits: dict[str, int] = {}
        try:
            context_window = int(metrics['context_window_tokens'])
            input_tokens = int(metrics['estimated_input_tokens'])
            reserved_output = int(metrics['reserved_output_tokens'])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            remaining_tokens = max(16, context_window - input_tokens - reserved_output)
            output_limits['max_tokens'] = min(self.output_manager.max_tokens, remaining_tokens)

        try:
            request_chars = int(metrics['serialized_chars'])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            # Leave room for the tool-result envelope, IDs, and truncation metadata.
            remaining_chars = max(256, limits.max_json_chars - request_chars - 512)
            output_limits['max_chars'] = min(self.output_manager.max_chars, remaining_chars)
        return output_limits

    async def fail_tool_call(
        self,
        turn_id: str,
        call_id: str,
        error: dict,
        *,
        uncertain: bool,
    ) -> Any:
        return await self.repository.fail_tool_call(
            turn_id,
            call_id,
            _json_safe(error),
            uncertain=uncertain,
        )

    async def recover_expired_turns(self) -> int:
        recovered = await self.repository.recover_expired_turns()
        if recovered:
            log.warning('Recovered expired agent turns: count=%d', recovered)
        return recovered

    async def _heartbeat(self, turn_id: str, stop: asyncio.Event) -> None:
        interval = max(5, self.lease_seconds // 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                alive = await self.repository.heartbeat_turn(turn_id, self.worker_id, lease_seconds=self.lease_seconds)
                if not alive:
                    return

    async def _run_handler_until_cancelled(self, handler: Callable[[], Awaitable[Any]], turn_id: str) -> Any:
        task = asyncio.create_task(handler())
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.5)
                if done:
                    return task.result()
                if await self.repository.is_cancel_requested(turn_id):
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise asyncio.CancelledError
        finally:
            if not task.done():
                task.cancel()

    async def _track_stream(
        self,
        response: StreamingResponse,
        turn_id: str,
        owner_id: str,
        session_id: str,
        heartbeat_stop: asyncio.Event,
        heartbeat_task: asyncio.Task,
    ) -> StreamingResponse:
        iterator = response.body_iterator

        async def tracked():
            completed = False
            try:
                while True:
                    try:
                        chunk = await self._next_stream_chunk_or_cancel(iterator, turn_id)
                    except StopAsyncIteration:
                        break
                    yield chunk
                completed = True
            except asyncio.CancelledError:
                unknown = await self.repository.interrupt_running_tool_calls(turn_id)
                cancel_requested = await self.repository.is_cancel_requested(turn_id)
                final_status = 'paused' if unknown else 'cancelled' if cancel_requested else 'interrupted'
                await self.repository.set_turn_status(
                    turn_id,
                    final_status,
                    event_type='turn.recovery_required' if unknown else 'turn.cancelled' if cancel_requested else 'turn.interrupted',
                    payload={'turn_id': turn_id, 'reason': 'stream_cancelled'},
                )
                await self._update_task_status(session_id, owner_id, turn_id, final_status)
                raise
            except BaseException as error:
                await self._fail_turn(turn_id, _error_payload(error))
                current = await self.repository.get_turn(turn_id)
                await self._update_task_status(session_id, owner_id, turn_id, current.status if current else 'failed')
                raise
            else:
                if completed:
                    if await self.repository.has_unresolved_tool_calls(turn_id):
                        await self.repository.set_turn_status(
                            turn_id,
                            'waiting_approval',
                            event_type='turn.waiting_approval',
                            payload={'turn_id': turn_id},
                        )
                        await self._update_task_status(session_id, owner_id, turn_id, 'waiting_approval')
                    else:
                        await self.repository.complete_turn(turn_id)
                        await self._update_task_status(session_id, owner_id, turn_id, 'completed')
            finally:
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        response.body_iterator = tracked()
        return response

    async def _next_stream_chunk_or_cancel(self, iterator, turn_id: str) -> Any:
        next_chunk = asyncio.create_task(iterator.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({next_chunk}, timeout=0.5)
                if done:
                    return next_chunk.result()
                if await self.repository.is_cancel_requested(turn_id):
                    next_chunk.cancel()
                    try:
                        await next_chunk
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass
                    raise asyncio.CancelledError
        finally:
            if not next_chunk.done():
                next_chunk.cancel()

    async def _fail_turn(self, turn_id: str, error: dict) -> None:
        turn = await self.repository.get_turn(turn_id)
        if turn and turn.status not in {'completed', 'failed', 'cancelled'}:
            if await self.repository.has_unresolved_tool_calls(turn_id):
                unknown = await self.repository.has_unknown_tool_outcomes(turn_id)
                await self.repository.set_turn_status(
                    turn_id,
                    'paused' if unknown else 'interrupted',
                    event_type='turn.recovery_required' if unknown else 'turn.interrupted',
                    payload={'turn_id': turn_id, 'error': error},
                )
                return
            await self.repository.set_turn_status(
                turn_id,
                'failed',
                event_type='turn.failed',
                payload={'turn_id': turn_id, 'error': error},
                error=error,
            )

    async def _update_task_status(self, session_id: str, owner_id: str, turn_id: str, status: str) -> None:
        await self.repository.update_session_turn_status(
            session_id,
            owner_id=owner_id,
            turn_id=turn_id,
            status=status,
        )

def _configured_tool_output_limit() -> int:
    raw = os.getenv('AGENT_TOOL_OUTPUT_MAX_TOKENS', '10000')
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError('AGENT_TOOL_OUTPUT_MAX_TOKENS must be a positive integer') from error
    if value < 1:
        raise ValueError('AGENT_TOOL_OUTPUT_MAX_TOKENS must be a positive integer')
    return value


def _chat_start_idempotency_key(chat_message_id: str | None, user_message_id: str | None) -> str | None:
    stable_id = chat_message_id or user_message_id
    if not stable_id:
        return None
    value = f'chat:{stable_id}'
    if len(value) <= 255:
        return value
    import hashlib

    return f'chat:sha256:{hashlib.sha256(value.encode("utf-8")).hexdigest()}'


def _should_persist_client_event(event_data: Any) -> bool:
    if not isinstance(event_data, dict):
        return False
    event_type = event_data.get('type')
    if event_type in {
        'chat:message',
        'chat:message:error',
        'chat:message:follow_ups',
        'chat:messages',
        'chat:tasks',
        'chat:tasks:cancel',
    }:
        return True
    if event_type not in {'chat:completion', 'response:completion'}:
        return False
    data = event_data.get('data')
    return isinstance(data, dict)


def _is_retry_safe_builtin(name: str, tool: dict) -> bool:
    if tool.get('type') != 'builtin' or tool.get('direct'):
        return False
    return name in _SAFE_BUILTINS


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 30:
        return '[omitted: maximum serialization depth exceeded]'
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if callable(value):
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            if name.lower().replace('-', '_') in _SECRET_KEYS or callable(item):
                continue
            cleaned = _json_safe(item, depth=depth + 1)
            if cleaned is not None:
                result[name] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in value if not callable(item)]
    if hasattr(value, 'model_dump'):
        return _json_safe(value.model_dump(mode='json'), depth=depth + 1)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _latest_user_text(payload: dict) -> str:
    messages = payload.get('messages') or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get('role') == 'user':
            content = message.get('content') or ''
            if isinstance(content, list):
                return ' '.join(str(item.get('text') or '') for item in content if isinstance(item, dict))
            return str(content)
    return str(payload.get('user_message') or '')


def _initial_task_state(payload: dict, user_message_id: str | None) -> dict:
    return {
        'version': 1,
        'status': 'in_progress',
        'objective': _latest_user_text(payload),
        'completed_steps': [],
        'important_files': [],
        'decisions': [],
        'modified_files': [],
        'tests': [],
        'pending_actions': ['Complete the active chat turn'],
        'latest_user_message_id': user_message_id,
        'updated_at': dt.datetime.now(dt.UTC).isoformat(),
    }


def _error_payload(error: BaseException) -> dict:
    if isinstance(error, HTTPException):
        return {
            'code': 'http_error',
            'status_code': error.status_code,
            'message': str(error.detail)[:2000],
        }
    return {'code': 'agent_turn_failed', 'message': type(error).__name__}
