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
    'get_document_task': 'read',
    'propose_document_edit': 'propose',
    'publish_document_output': 'create',
    'commit_preview': 'commit',
}

_EVIDENCE_KEYS = ('evidence_id', 'evidence_ids')


def infer_required_action(prompt: str) -> str:
    """Choose a conservative completion requirement from the user's request."""
    text = (prompt or '').casefold()
    content_filtered_inventory = re.search(
        r'\b(?:files?|documents?|resources?)\b.{0,30}\b(?:that|which)?\s*'
        r'(?:mention|mentions|contain|contains|include|includes|match|matches)\b|'
        r'\b(?:mentioning|containing|including|matching)\b|'
        r'(?:file|tệp|tài liệu).{0,30}(?:chứa|đề cập|nhắc đến|nói về|liên quan)',
        text,
    )
    if content_filtered_inventory:
        return 'search'
    if re.search(
        r'\b(?:list|enumerate)\b.{0,60}\b(?:files?|documents?|resources?)\b.{0,40}'
        r'\b(?:attached|uploaded|available|in this chat|in the chat)\b|'
        r'\b(?:list|enumerate|show)\b.{0,40}\b(?:attached|uploaded|available)\b.{0,20}'
        r'\b(?:files?|documents?|resources?)\b|'
        r'\b(?:what|which)\s+(?:files?|documents?|resources?)\s+(?:are|is)\s+'
        r'(?:attached|uploaded|available|in this chat)\b|'
        r'liệt kê.{0,50}(?:file|tệp|tài liệu).{0,30}(?:đính kèm|trong (?:cuộc trò chuyện|chat))|'
        r'(?:file|tệp|tài liệu) nào.{0,30}(?:đính kèm|được chia sẻ)',
        text,
    ):
        return 'discover'
    if re.search(
        r'\b(commit|approve|apply)\b|\b(?:save|write)\s+(?:the\s+)?(?:changes?|edits?)\b|'
        r'duyệt|phê duyệt|áp dụng|xác nhận|lưu (?:các )?(?:thay đổi|chỉnh sửa)',
        text,
    ):
        return 'commit'
    if re.search(r'\b(edit|change|update|rewrite|fill|replace)\b|sửa|đổi|cập nhật|điền|thay thế', text):
        return 'propose'
    create_match = re.search(
        r'\b(?:create|generate|export|make)\b.{0,60}\b(?:file|document|spreadsheet|workbook|xlsx|csv|pdf|docx|pptx|artifact)\b|'
        r'\bconvert\b.{0,60}\b(?:to|into)\s+(?:a\s+)?(?:file|document|spreadsheet|workbook|xlsx|csv|pdf|docx|pptx)\b|'
        r'tạo.{0,60}(?:file|tệp|tài liệu|bảng tính)|xuất.{0,40}(?:file|tệp|tài liệu)|chuyển đổi.{0,40}(?:file|tệp|tài liệu)',
        text,
    )
    if create_match and not re.search(
        r'\b(?:summary|overview|analysis|report|answer)\s+of\b|tóm tắt\s+(?:của|về)|phân tích\s+(?:của|về)',
        create_match.group(0),
    ):
        return 'create'
    if re.search(r'\b(calculate|compute|sum|average|count)\b|tính|tổng|trung bình|đếm', text):
        return 'calculate'
    if re.search(r'\b(find|search|where|which)\b|tìm|ở đâu|nào', text):
        return 'search'
    if re.search(
        r'\b(read|summari[sz]e|summary|contents?|explain|analy[sz]e)\b|'
        r'\bwhat\b.{0,24}\b(?:in|inside|within)\b|'
        r'\b(?:contain|contains|inside|within)\b|đọc|tóm tắt|nội dung|dữ liệu|bên trong|phân tích',
        text,
    ):
        return 'read'
    if re.search(r'\b(open|show|display|preview)\b|mở|hiển thị|xem file', text):
        return 'display'
    return 'read'


def initialize_task_state(prompt: str, *, required_action: str | None = None) -> dict:
    primary = required_action or infer_required_action(prompt)
    actions = [primary]
    if not required_action and primary == 'propose':
        from .resource_aware import requires_resource_evidence

        if requires_resource_evidence(prompt):
            actions.insert(0, 'read')
    content_request = bool(
        re.search(
            r'\bwhat\b.{0,24}\b(?:in|inside|within)\b|'
            r'\b(?:contents?|data|text|information|rows?|values?)\b|'
            r'nội dung|dữ liệu|bên trong|có gì',
            prompt or '',
            re.IGNORECASE,
        )
    )
    explicit_display = bool(re.search(r'\b(open|display|preview)\b|mở|hiển thị', prompt or '', re.IGNORECASE))
    show_file = bool(
        re.search(r'\bshow\b.{0,30}\b(?:file|document|spreadsheet|workbook)\b', prompt or '', re.IGNORECASE)
    )
    if not required_action and primary != 'display' and (explicit_display or (show_file and not content_request)):
        actions.append('display')
    return {
        'goal': prompt or '',
        'required_action': primary,
        'required_actions': actions,
        'steps': [],
        'evidence_ids': [],
        'covered_resource_versions': {},
        'coverage_complete': False,
        'status': 'ready',
    }


def task_checkpoint(state: dict | None) -> dict | None:
    """Return a compact, content-free checkpoint safe to persist on a chat message."""
    if not isinstance(state, dict):
        return None
    step_fields = (
        'tool',
        'action',
        'status',
        'evidence_ids',
        'has_content',
        'effect_confirmed',
        'revision',
        'coverage_complete',
        'search_complete',
        'truncated',
        'next_offset',
        'next_start_line',
    )
    steps = [
        {key: step[key] for key in step_fields if key in step}
        for step in state.get('steps', [])
        if isinstance(step, dict)
    ]
    checkpoint = {
        'schema_version': int(state.get('schema_version', 1)),
        'goal': state.get('goal', ''),
        'required_action': state.get('required_action', 'read'),
        'required_actions': list(state.get('required_actions') or [state.get('required_action', 'read')]),
        'steps': steps,
        'evidence_ids': list(dict.fromkeys(state.get('evidence_ids') or [])),
        'status': state.get('status', 'ready'),
        'requires_complete_coverage': bool(state.get('requires_complete_coverage')),
        'coverage_complete': bool(state.get('coverage_complete')),
        'resource_scope_ids': list(state.get('resource_scope_ids') or []),
        'resource_scope_versions': dict(state.get('resource_scope_versions') or {}),
        'covered_resource_versions': dict(state.get('covered_resource_versions') or {}),
    }
    for key in ('reason', 'budget'):
        if key in state:
            checkpoint[key] = state[key]
    return checkpoint


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
    decoded = _decoded_result(result)
    records = list(_walk(decoded))
    action = _TOOL_ACTIONS.get(tool_name, 'other')
    if tool_name == 'publish_document_output' and any(item.get('preview_id') for item in records):
        action = 'propose'
    blocked = any(
        item.get('error')
        or item.get('status') in {'blocked', 'failed', 'error', 'cancelled'}
        or item.get('code') in {'resource_read_limit', 'resource_task_time_limit', 'tool_result_too_large'}
        for item in records
    )
    blocked = (
        blocked
        or (
            isinstance(decoded, str)
            and bool(re.match(r'\s*(?:error\b|failed\b|permission denied\b)', decoded, re.IGNORECASE))
        )
        or any(isinstance(item.get('exit_code'), int) and item['exit_code'] != 0 for item in records)
    )
    pending = any(item.get('status') in {'pending', 'processing', 'queued'} for item in records)
    evidence_ids = []
    for item in records:
        for key in _EVIDENCE_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                evidence_ids.append(value)
            elif isinstance(value, list):
                evidence_ids.extend(entry for entry in value if isinstance(entry, str) and entry)
    has_content = any(
        bool(item.get(key)) for item in records for key in ('text', 'content', 'document', 'result', 'stdout', 'output')
    )
    coverage_complete = isinstance(decoded, dict) and decoded.get('coverage_complete') is True
    search_complete = any(item.get('search_complete') is True for item in records)
    if isinstance(decoded, list) and decoded:
        metadata_sets = [
            source.get('metadata')
            for source in decoded
            if isinstance(source, dict) and isinstance(source.get('metadata'), list) and source['metadata']
        ]
        coverage_complete = (
            bool(metadata_sets)
            and len(metadata_sets) == len(decoded)
            and all(
                isinstance(item, dict) and item.get('coverage_complete') is True
                for metadata in metadata_sets
                for item in metadata
            )
        )
    effect_confirmed = False
    if action == 'display':
        effect_confirmed = any(item.get('displayed') is True for item in records)
    elif action == 'discover':
        effect_confirmed = (
            not blocked
            and not pending
            and (
                isinstance(decoded, list)
                or any(
                    item.get('coverage_complete') is True and isinstance(item.get(key), list)
                    for item in records
                    for key in ('files', 'documents', 'resources')
                )
            )
        )
    elif action == 'propose':
        effect_confirmed = any(
            isinstance(item.get(key), str) and item[key] for item in records for key in ('preview_id', 'proposal_id')
        )
    elif action == 'create':
        effect_confirmed = any(
            isinstance(item.get('document_id'), str)
            and item.get('document_id')
            and isinstance(item.get('revision'), int)
            for item in records
        )
    elif action == 'commit':
        effect_confirmed = any(item.get('committed') is True or item.get('status') == 'committed' for item in records)
    elif action == 'search':
        # A positive result needs provenance. An empty result is only meaningful
        # when the adapter confirms that the requested scope was fully searched.
        if not search_complete and tool_name != 'search_editable_document':
            # Compatibility with older search adapters whose coverage field
            # meant that the requested query finished over its full scope.
            search_complete = coverage_complete
        effect_confirmed = bool(evidence_ids) or search_complete
    step = {
        'tool': tool_name,
        'action': action,
        'status': 'blocked' if blocked else 'pending' if pending else 'succeeded',
        'evidence_ids': list(dict.fromkeys(evidence_ids)),
        'has_content': has_content,
        'effect_confirmed': effect_confirmed,
    }
    for key in ('revision', 'coverage_complete', 'search_complete', 'truncated', 'next_offset', 'next_start_line'):
        for item in records:
            if key in item:
                step[key] = item[key]
                break
    if action == 'search':
        step['search_complete'] = search_complete
    state.setdefault('steps', []).append(step)
    for evidence_id in step['evidence_ids']:
        if evidence_id not in state.setdefault('evidence_ids', []):
            state['evidence_ids'].append(evidence_id)
    if action in {'read', 'search'} and step['status'] == 'succeeded':
        references = _verified_coverage_references(tool_name, decoded)
        if references:
            covered = dict(state.get('covered_resource_versions') or {})
            covered.update(references)
            state['covered_resource_versions'] = covered
            state['coverage_complete'] = _scope_coverage_complete(state)
    state['status'] = 'blocked' if blocked else 'pending' if pending else 'in_progress'
    return step


def _valid_revision(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _verified_coverage_references(tool_name: str, decoded) -> dict[str, int]:
    """Return resource/version pairs whose complete contents reached this result."""
    references = {}
    if tool_name == 'get_document_task' and isinstance(decoded, dict):
        if (
            decoded.get('status') == 'completed'
            and decoded.get('coverage_complete') is True
            and decoded.get('result')
            and decoded.get('evidence_ids')
            and decoded.get('resource_id')
            and _valid_revision(decoded.get('revision'))
        ):
            references[str(decoded['resource_id'])] = decoded['revision']
        return references

    if not isinstance(decoded, list):
        return references
    for source in decoded:
        if not isinstance(source, dict):
            continue
        metadata = source.get('metadata')
        if not isinstance(metadata, list) or not metadata:
            continue
        if any(
            not isinstance(item, dict)
            or item.get('coverage_complete') is not True
            or item.get('truncated') is True
            or not _valid_revision(item.get('revision'))
            for item in metadata
        ):
            continue
        resource_id = (source.get('source') or {}).get('id') or metadata[0].get('file_id')
        revisions = {item['revision'] for item in metadata}
        if resource_id and len(revisions) == 1:
            references[str(resource_id)] = revisions.pop()
    return references


def _scope_coverage_complete(state: dict) -> bool:
    required = list(dict.fromkeys(str(item) for item in state.get('resource_scope_ids') or []))
    covered = state.get('covered_resource_versions') or {}
    expected = state.get('resource_scope_versions') or {}
    return bool(required) and all(
        resource_id in covered and (expected.get(resource_id) is None or covered[resource_id] == expected[resource_id])
        for resource_id in required
    )


def reconcile_resource_coverage(
    state: dict,
    resource_scope_ids: list[str],
    current_versions: dict[str, int],
    *,
    preserve: bool,
) -> None:
    """Keep only full-content evidence that still matches the current scoped versions."""
    previous = state.get('covered_resource_versions') or {}
    scope_ids = list(dict.fromkeys(str(item) for item in resource_scope_ids))
    previous_scope_ids = list(dict.fromkeys(str(item) for item in state.get('resource_scope_ids') or []))
    previous_versions = state.get('resource_scope_versions') or {}
    revisions_changed = preserve and (
        set(previous_scope_ids) != set(scope_ids)
        or any(
            resource_id not in current_versions or previous_versions.get(resource_id) != current_versions[resource_id]
            for resource_id in set(previous_scope_ids) | set(scope_ids)
        )
    )
    if revisions_changed:
        state['steps'] = []
        state['evidence_ids'] = []
    if preserve:
        covered = {
            str(resource_id): revision
            for resource_id, revision in previous.items()
            if str(resource_id) in current_versions
            and current_versions[str(resource_id)] == revision
            and not revisions_changed
        }
    else:
        covered = {}
    state['resource_scope_ids'] = scope_ids
    state['resource_scope_versions'] = dict(current_versions)
    state['covered_resource_versions'] = covered
    state['coverage_complete'] = _scope_coverage_complete(state)


def can_answer_task(state: dict) -> bool:
    actions = state.get('required_actions') or [state.get('required_action', 'read')]
    steps = state.get('steps') or []
    successful = [step for step in steps if step.get('status') == 'succeeded']

    def satisfied(action):
        if action in {'discover', 'display', 'propose', 'create', 'commit', 'search'}:
            return any(step.get('action') == action and step.get('effect_confirmed') is True for step in successful)
        if action == 'calculate':
            return any(
                step.get('action') == 'calculate' and step.get('evidence_ids') and step.get('has_content')
                for step in successful
            )
        return any(
            step.get('action') in {'read', 'search'} and step.get('evidence_ids') and step.get('has_content')
            for step in successful
        )

    return all(satisfied(action) for action in actions)
