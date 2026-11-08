"""Request-scoped task and tool outcome tracking for evidence-based answers."""

import json
import re


_TOOL_ACTIONS = {
    'list_chat_files': 'discover',
    'list_editable_documents': 'discover',
    'display_file': 'display',
    'view_file': 'read',
    'read_editable_document': 'read',
    'view_knowledge_file': 'read',
    'grep_chat_files': 'search',
    'search_editable_document': 'search',
    'search_knowledge_files': 'search',
    'query_chat_files': 'search',
    'query_knowledge_files': 'search',
    'run_document_code': 'calculate',
    'run_skill_script': 'calculate',
    'propose_document_edit': 'propose',
    'publish_document_output': 'commit',
    'commit_preview': 'commit',
}

_EVIDENCE_KEYS = ('evidence_id', 'evidence_ids')


def infer_required_action(prompt: str) -> str:
    """Choose a conservative completion requirement from the user's request."""
    text = (prompt or '').casefold()
    if re.search(r'\b(edit|change|update|save|write)\b|sửa|đổi|cập nhật|lưu', text):
        return 'commit'
    if re.search(r'\b(calculate|compute|sum|average|count)\b|tính|tổng|trung bình|đếm', text):
        return 'calculate'
    if re.search(r'\b(find|search|where|which)\b|tìm|ở đâu|nào', text):
        return 'search'
    if re.search(r'\b(read|summari[sz]e|summary|contents?|explain|analy[sz]e)\b|đọc|tóm tắt|nội dung|phân tích', text):
        return 'read'
    if re.search(r'\b(open|show|display|preview)\b|mở|hiển thị|xem file', text):
        return 'display'
    return 'read'


def initialize_task_state(prompt: str, *, required_action: str | None = None) -> dict:
    primary = required_action or infer_required_action(prompt)
    actions = [primary]
    if not required_action and primary != 'display' and re.search(
        r'\b(open|show|display|preview)\b|mở|hiển thị', prompt or '', re.IGNORECASE
    ):
        actions.append('display')
    return {
        'goal': prompt or '',
        'required_action': primary,
        'required_actions': actions,
        'steps': [],
        'evidence_ids': [],
        'status': 'ready',
    }


def _decoded_result(result):
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
            return decoded
        except (json.JSONDecodeError, TypeError):
            return result
    return result


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def record_tool_result(state: dict, tool_name: str, result) -> dict:
    """Record tool effect without retaining raw document contents."""
    action = _TOOL_ACTIONS.get(tool_name, 'other')
    decoded = _decoded_result(result)
    records = list(_walk(decoded))
    blocked = any(
        item.get('error') or item.get('status') in {'blocked', 'failed', 'error'}
        or item.get('code') in {'resource_read_limit', 'resource_task_time_limit'}
        for item in records
    )
    blocked = blocked or (isinstance(decoded, str) and bool(re.match(
        r'\s*(?:error\b|failed\b|permission denied\b)', decoded, re.IGNORECASE
    ))) or any(isinstance(item.get('exit_code'), int) and item['exit_code'] != 0 for item in records)
    pending = any(item.get('status') in {'pending', 'processing', 'queued'} for item in records)
    evidence_ids = []
    for item in records:
        for key in _EVIDENCE_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                evidence_ids.append(value)
            elif isinstance(value, list):
                evidence_ids.extend(entry for entry in value if isinstance(entry, str) and entry)
    step = {
        'tool': tool_name,
        'action': action,
        'status': 'blocked' if blocked else 'pending' if pending else 'succeeded',
        'evidence_ids': list(dict.fromkeys(evidence_ids)),
        'has_content': any(
            bool(item.get('text') or item.get('content') or item.get('document')) for item in records
        ),
    }
    for key in ('revision', 'coverage_complete', 'truncated', 'next_offset', 'next_start_line'):
        for item in records:
            if key in item:
                step[key] = item[key]
                break
    state.setdefault('steps', []).append(step)
    for evidence_id in step['evidence_ids']:
        if evidence_id not in state.setdefault('evidence_ids', []):
            state['evidence_ids'].append(evidence_id)
    state['status'] = 'blocked' if blocked else 'pending' if pending else 'in_progress'
    return step


def can_answer_task(state: dict) -> bool:
    actions = state.get('required_actions') or [state.get('required_action', 'read')]
    steps = state.get('steps') or []
    successful = [step for step in steps if step.get('status') == 'succeeded']
    def satisfied(action):
        if action in {'display', 'commit', 'search'}:
            return any(step.get('action') == action for step in successful)
        if action == 'calculate':
            return any(step.get('action') == 'calculate' and step.get('evidence_ids') for step in successful)
        return any(step.get('action') in {'read', 'search'} and step.get('evidence_ids')
                   and step.get('has_content') for step in successful)
    return all(satisfied(action) for action in actions)
