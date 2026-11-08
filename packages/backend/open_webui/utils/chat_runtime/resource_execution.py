"""Compatibility adapter for resource-aware task evidence and answer gating."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline
from open_webui.mini_hermes.resource_aware import bounded_tool_output
from open_webui.utils.chat_runtime.tool_results import tool_result_content


def _execution_enabled() -> bool:
    return os.getenv('MH_AGENT_EXECUTION_V1', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def prepare_resource_tool_result(metadata: dict, tool_name: str, result: Any) -> tuple[str, bool]:
    """Serialize, bound, and record only the result actually passed back to the model."""
    content = tool_result_content(result)
    if not metadata.get('resource_aware_files'):
        return content, True

    bounded = bounded_tool_output(
        content,
        max_chars=max(1, int(metadata.get('resource_file_context_char_budget', 12_000))),
    )
    supplied = isinstance(bounded, str)
    if supplied:
        bounded_content = bounded
    else:
        from open_webui.utils.json_codec import JSONCodec

        bounded_content = JSONCodec.dumps(bounded, ensure_ascii=False)

    state = metadata.get('agent_task_state')
    if isinstance(state, dict):
        from open_webui.mini_hermes.agent_execution import record_tool_result

        # An oversized result is a blocked observation, not evidence.
        record_tool_result(state, tool_name, bounded if supplied else bounded)
    return bounded_content, supplied


def resource_task_state_metadata(metadata: dict) -> dict:
    """Small content-free checkpoint stored with resumable assistant messages."""
    from open_webui.mini_hermes.agent_execution import task_checkpoint

    checkpoint = task_checkpoint(metadata.get('agent_task_state'))
    return {'agent_task_state': checkpoint} if checkpoint else {}


class ResourceAnswerGate:
    """Hold provisional prose until resource task requirements have evidence."""

    def __init__(self, metadata: dict, *, enabled: bool | None = None):
        self.metadata = metadata
        self.enabled = _execution_enabled() if enabled is None else enabled

    @property
    def active(self) -> bool:
        return self.enabled and bool(
            self.metadata.get('resource_execution_requested') or self.metadata.get('resource_evidence_requested')
        )

    @staticmethod
    def _is_hidden_prose_item(item: dict) -> bool:
        if item.get('type') == 'reasoning':
            return True
        if item.get('type') != 'message':
            return False
        content = item.get('content')
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return any(
                isinstance(part, dict)
                and part.get('type') in {'output_text', 'text'}
                and isinstance(part.get('text'), str)
                and bool(part['text'].strip())
                for part in content
            )
        return False

    def present(self, output: Any, *, final: bool = False) -> Any:
        if not self.active or not isinstance(output, list):
            return output

        if not final:
            # Keep visible actions and tool progress; do not expose model prose
            # that may claim the resource was read before evidence exists.
            return [deepcopy(item) for item in output if not self._is_hidden_prose_item(item)]

        decision = ResourceRequestPipeline.decision(self.metadata)
        if decision.status == 'completed':
            return output

        visible = [deepcopy(item) for item in output if not self._is_hidden_prose_item(item)]
        text = self.fallback_text(decision.status, decision.reason)

        visible.append(
            {
                'type': 'message',
                'status': 'completed',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': text}],
            }
        )
        self.metadata['resource_completion'] = decision.as_dict()
        return visible

    @staticmethod
    def fallback_text(status: str, reason: str = '') -> str:
        if reason == 'ambiguous_resource_scope':
            return 'Có nhiều tệp có thể phù hợp. Bạn muốn hỏi tệp nào?'
        if status == 'pending':
            return 'Tác vụ đọc tài liệu vẫn đang xử lý; chưa có câu trả lời cuối cùng.'
        if status == 'blocked':
            return (
                'Tôi chưa thể hoàn tất vì thao tác đọc tài liệu bị chặn. '
                'Hãy kiểm tra quyền truy cập hoặc trạng thái tệp.'
            )
        if reason == 'additional_resource_evidence_required':
            return 'Tôi chưa thu thập đủ nội dung tài liệu để xác nhận câu trả lời.'
        return 'Tôi mới kiểm tra được một phần tài liệu nên chưa thể xác nhận câu trả lời đầy đủ.'

    def fallback_text_for_current_state(self) -> str:
        decision = ResourceRequestPipeline.decision(self.metadata)
        return self.fallback_text(decision.status, decision.reason)

    def filter_provider_payload(self, payload: dict) -> dict | None:
        """Suppress raw provider prose for stream paths without the tool loop."""
        if not self.active or not isinstance(payload, dict):
            return payload
        if self._evidence_complete():
            return payload

        payload = deepcopy(payload)
        event_type = str(payload.get('type') or '')
        if event_type in {
            'response.output_text.delta',
            'response.reasoning_text.delta',
            'response.reasoning_summary_text.delta',
            'response.refusal.delta',
        }:
            return None

        return self._suppress_provider_payload_text(payload)

    def _suppress_provider_payload_text(self, payload: dict) -> dict:
        response = payload.get('response')
        if isinstance(response, dict) and isinstance(response.get('output'), list):
            response['output'] = self.present(response['output'])

        self._suppress_choice_text(payload.get('choices'))
        return payload

    @staticmethod
    def _suppress_choice_text(choices: Any) -> None:
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                for key in ('message', 'delta'):
                    part = choice.get(key)
                    if isinstance(part, dict):
                        if isinstance(part.get('content'), str):
                            part['content'] = ''
                        for reasoning_key in ('reasoning', 'reasoning_content'):
                            if isinstance(part.get(reasoning_key), str):
                                part[reasoning_key] = ''

    def _evidence_complete(self) -> bool:
        return ResourceRequestPipeline.decision(self.metadata).status == 'completed'

    def present_event(self, event: dict) -> dict | None:
        """Filter externally visible completion events while leaving internal model data intact."""
        if not self.active or not isinstance(event, dict):
            return event

        event = deepcopy(event)
        event_type = event.get('type', '')
        if event_type not in {'chat:completion', 'response:completion'}:
            return event

        data = event.get('data')
        if not isinstance(data, dict):
            return event
        return self._present_completion_event(event, data, event_type)

    def _present_completion_event(self, event: dict, data: dict, event_type: str) -> dict | None:
        final = data.get('done') is True

        self._present_output_fields(data, final=final)

        item = data.get('item')
        if isinstance(item, dict) and item.get('type') in {'message', 'reasoning'} and not self._evidence_complete():
            # Item events are incremental. The sanitized final snapshot carries
            # the fallback status if the evidence gate is still unsatisfied.
            return None

        event_name = str(data.get('type') or event_type).casefold()
        if self._is_text_delta(event_name) and not self._evidence_complete():
            return None

        if not self._evidence_complete():
            self._suppress_choice_text(data.get('choices'))

        event['data'] = data
        return event

    def _present_output_fields(self, data: dict, *, final: bool) -> None:
        if isinstance(data.get('output'), list):
            data['output'] = self.present(data['output'], final=final)

        response = data.get('response')
        if isinstance(response, dict) and isinstance(response.get('output'), list):
            response['output'] = self.present(response['output'], final=final)

    @staticmethod
    def _is_text_delta(event_name: str) -> bool:
        return any(marker in event_name for marker in ('output_text.delta', 'text.delta', 'reasoning.delta'))

    def bind(self, emitter):
        if emitter is None or not self.active:
            return emitter

        async def emit(event):
            presented = self.present_event(event)
            if presented is not None:
                return await emitter(presented)
            return None

        return emit
