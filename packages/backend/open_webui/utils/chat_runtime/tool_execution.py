"""Execution adapter for saved tool-call items in a chat response."""

from __future__ import annotations

import ast
import logging
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.tools import get_updated_tool_function

log = logging.getLogger(__name__)


def _parse_tool_arguments(name: str, tool_call: dict) -> tuple[dict | None, str | None]:
    raw_arguments = tool_call.get('arguments', '{}')
    if raw_arguments and raw_arguments.strip():
        try:
            params = JSONCodec.loads(raw_arguments)
        except Exception:
            try:
                params = ast.literal_eval(raw_arguments)
            except Exception as error:
                log.debug(error)
                return None, 'Error: Tool arguments are not valid JSON.'
    else:
        params = {}

    if not isinstance(params, dict):
        return None, f'Error: Tool call arguments for `{name}` must be a JSON object. Please try again.'
    return params, None


def _resource_tool_block(name: str, params: dict, tool: dict, metadata: dict) -> dict | None:
    if not metadata.get('resource_aware_files'):
        return None

    from open_webui.mini_hermes.agent_execution import _TOOL_ACTIONS
    from open_webui.mini_hermes.execution.contracts import ExecutionBudget

    task_action = _TOOL_ACTIONS.get(name, 'other')
    if metadata.get('resource_greeting') and task_action != 'other':
        return {
            'code': 'resource_access_not_requested',
            'status': 'blocked',
            'message': 'The current message is an independent greeting; do not inspect chat resources.',
        }
    if metadata.get('resource_scope_ambiguous') and task_action not in {'discover', 'other'}:
        return {
            'code': 'ambiguous_resource_scope',
            'status': 'blocked',
            'message': 'Ask which attached file the user means before reading or changing a resource.',
        }

    task_state = metadata.get('agent_task_state') or {}
    budget_data = task_state.get('budget') or ExecutionBudget().as_dict()
    budget = ExecutionBudget(**budget_data)
    elapsed = time.monotonic() - metadata.get('_resource_task_started_at', time.monotonic())
    if time.time() >= budget.deadline or elapsed >= 300:
        return {
            'code': 'resource_task_time_limit',
            'message': 'File task time limit reached (5 minutes). Continue with a narrower request.',
        }
    if task_action in {'read', 'search'} and budget.reads >= budget.max_reads:
        return {
            'code': 'resource_read_limit',
            'message': 'File reading limit reached (24 calls). Narrow the question or continue in a new request.',
        }
    if task_action in {'read', 'search'}:
        budget.reads += 1
        task_state['budget'] = budget.as_dict()
        metadata['agent_task_state'] = task_state
    return None


def _resolve_tool_target(name: str, params: dict, tool: dict) -> tuple[dict, dict, str, bool, bool]:
    if name == 'display_file' and (
        str(params.get('path', '')).startswith('/api/v1/files/')
        or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', str(params.get('path', '')))
    ):
        if tool.get('file_tool'):
            tool = tool['file_tool']
        elif tool.get('type') == 'terminal' or tool.get('server', {}).get('is_terminal') is True:
            return params, tool, tool.get('type', ''), tool.get('direct', False), True

    tool_type = tool.get('type', '')
    direct_tool = tool.get('direct', False)
    if tool_type != 'mcp':
        allowed_params = tool.get('spec', {}).get('parameters', {}).get('properties', {}).keys()
        params = {key: value for key, value in params.items() if key in allowed_params}
    return params, tool, tool_type, direct_tool, False


async def _approval_denied(
    name: str, tool_call: dict, params: dict, metadata: dict, tool_type: str, event_caller
) -> bool:
    if (
        is_saved_chat_id(metadata.get('chat_id'))
        or metadata.get('params', {}).get('tool_approval_mode') != 'ask'
        or (name == 'ask_user' and tool_type == 'builtin')
    ):
        return False
    if not event_caller:
        tool_call['_agent_approval_decision'] = 'rejected'
        return True
    approved = (
        await event_caller(
            {
                'type': 'confirmation',
                'data': {'tool_call': {'id': tool_call.get('call_id'), 'name': name, 'arguments': params}},
            }
        )
        is True
    )
    tool_call['_agent_approval_decision'] = 'approved' if approved else 'rejected'
    return not approved  # Disconnection and timeout replies are error objects, not approval.


async def _invoke_tool(
    name: str, params: dict, tool: dict, direct_tool: bool, form_data: dict, metadata: dict, event_caller
):
    if direct_tool:
        if not event_caller:
            return 'Error: Browser session is not connected for this direct tool.'
        return await event_caller(
            {
                'type': 'execute:tool',
                'data': {
                    'id': str(uuid4()),
                    'name': name,
                    'params': params,
                    'server': tool.get('server', {}),
                    'session_id': metadata.get('session_id'),
                },
            }
        )

    function = await get_updated_tool_function(
        function=tool['callable'],
        extra_params={
            '__messages__': form_data.get('messages', []),
            '__files__': metadata.get('files') or [],
            '__resource_scope_ids__': (
                metadata.get('resource_scope_ids') if metadata.get('resource_execution_requested') else None
            ),
        },
    )
    return await function(**params)


async def execute_tool_call(form_data, metadata, event_caller, tool_call):
    """Execute a saved function_call item that was resolved before approval staging."""
    name = tool_call.get('name', '')
    params, error = _parse_tool_arguments(name, tool_call)
    if error:
        return {}, error, None, None, False

    tool_call['arguments'] = JSONCodec.dumps(params)
    tool = metadata.get('tools', {}).get(name)
    if not tool:
        return params, f'Error: Tool "{name}" not found.', None, None, False

    blocked = _resource_tool_block(name, params, tool, metadata)
    if blocked:
        return params, blocked, tool, tool.get('type'), tool.get('direct', False)

    params, tool, tool_type, direct_tool, unavailable = _resolve_tool_target(name, params, tool)
    if unavailable:
        return params, 'Error: Uploaded file display is not available.', None, None, False
    durable_runtime = None
    durable_turn_id = metadata.get('agent_turn_id')
    durable_call_id = tool_call.get('call_id') or tool_call.get('id')
    approval_denied = await _approval_denied(name, tool_call, params, metadata, tool_type, event_caller)
    durable_retry_safe = False
    if durable_turn_id and durable_call_id:
        from open_webui.agent_runtime.runtime import AgentRuntime

        durable_runtime = AgentRuntime.configured()
        approval_decision = tool_call.pop('_agent_approval_decision', None)
        if approval_decision is None and tool_call.get('approved') is True:
            approval_decision = 'approved'
        if approval_decision:
            await durable_runtime.record_tool_approval(
                durable_turn_id,
                owner_id=str(metadata.get('user_id') or ''),
                call_id=str(durable_call_id),
                tool_name=name,
                arguments=params,
                decision=approval_decision,
                approved_by=str(metadata.get('user_id') or ''),
                tool=tool,
            )
    if approval_denied:
        return params, 'Error: tool call was not approved.', tool, tool_type, direct_tool

    if durable_runtime:
        invocation, acquired = await durable_runtime.begin_tool_call(
            durable_turn_id,
            call_id=str(durable_call_id),
            tool_name=name,
            arguments=params,
            tool=tool,
        )
        if not acquired:
            return params, invocation.result_data, tool, tool_type, direct_tool
        durable_retry_safe = invocation.retry_safe

    tool_call['started_at'] = time.time()
    started = time.perf_counter()
    try:
        result = await _invoke_tool(name, params, tool, direct_tool, form_data, metadata, event_caller)
    except Exception as error:
        if durable_runtime:
            await durable_runtime.fail_tool_call(
                durable_turn_id,
                str(durable_call_id),
                {'code': 'tool_execution_failed', 'error_type': type(error).__name__},
                uncertain=not durable_retry_safe,
            )
            if not durable_retry_safe:
                raise HTTPException(
                    status_code=502,
                    detail={
                        'code': 'tool_outcome_unknown',
                        'message': 'Tool outcome is unknown. It was not retried.',
                    },
                ) from error
            durable_runtime = None
        result = {'error': str(error)}
    finally:
        tool_call['ended_at'] = time.time()
        tool_call['duration'] = time.perf_counter() - started
    if durable_runtime:
        result = await durable_runtime.complete_tool_call(
            durable_turn_id,
            str(durable_call_id),
            result,
            owner_id=str(metadata.get('user_id') or ''),
        )
    return params, result, tool, tool_type, direct_tool
