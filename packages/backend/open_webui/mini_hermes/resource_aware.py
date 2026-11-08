"""Request-scoped policy for uploaded files in chat."""

import hashlib
import os
import re


RESOURCE_FILE_TOOL_NAMES = frozenset(
    {
        'grep_chat_files',
        'view_file',
        'read_editable_document',
        'search_editable_document',
    }
)


def has_resource_file_tools(tools: dict | None) -> bool:
    """Whether this request actually has a scoped document read/search tool."""
    return bool(RESOURCE_FILE_TOOL_NAMES.intersection(tools or {}))


def resource_aware_file_ids(files: list[dict] | None) -> list[str]:
    """Return distinct uploaded file IDs; other resource kinds keep their own paths."""
    seen = set()
    result = []
    for item in files or []:
        if not isinstance(item, dict) or item.get('type', 'file') != 'file' or not item.get('id'):
            continue
        file_id = str(item['id'])
        if file_id not in seen:
            seen.add(file_id)
            result.append(file_id)
    return result


def resource_aware_context_enabled(files: list[dict] | None, *, has_chat_resources: bool = False) -> bool:
    return os.getenv('MH_RESOURCE_AWARE_FILES', 'true').lower() == 'true' and (
        bool(resource_aware_file_ids(files)) or has_chat_resources
    )


def resource_file_context_budget(context_window_tokens: int, max_output_tokens: int) -> int:
    """Reserve half the available input for history/system/tools; count source chars conservatively."""
    available_input_tokens = max(0, int(context_window_tokens) - int(max_output_tokens))
    return min(24_000, available_input_tokens // 2)


def is_independent_greeting(message: str, *, pending_task: bool) -> bool:
    if pending_task:
        return False
    return bool(re.fullmatch(r'\s*(?:hi|hello|hey|chào|xin chào)[!?.\s]*', message, re.IGNORECASE))


def plan_resource_context(message: str, *, has_pending_task: bool = False) -> str:
    """Deterministic first pass; final FULL/HIERARCHICAL choice also checks measured size."""
    if is_independent_greeting(message, pending_task=has_pending_task):
        return 'NONE'
    full_request = r'\b(full|entire|whole document|all pages|summari[sz]e|summary)\b|toàn bộ|tất cả|đầy đủ|tóm tắt'
    if re.search(full_request, message, re.IGNORECASE):
        return 'FULL'
    return 'FOCUSED'


def requires_resource_evidence(message: str, *, has_pending_task: bool = False) -> bool:
    """Whether the user asks about or continues work on an attached resource."""
    if has_pending_task:
        return True
    if is_independent_greeting(message, pending_task=False):
        return False
    references = (
        r'\b(summari[sz]e|summary|analy[sz]e|continue)\b|tóm tắt|phân tích|làm tiếp|tiếp tục|'
        r'\b(file|document|attachment|spreadsheet|workbook|sheet|page|slide|'
        r'content|data|this|these|it|second one|first one)\b|'
        r'\bwhat is (?:this|it|inside)\b|'
        r'file này|tài liệu này|tệp này|nội dung|sheet thứ|trang thứ|'
        r'cái này|phần này|file thứ|tệp thứ|tài liệu thứ'
    )
    return bool(re.search(references, message or '', re.IGNORECASE))


def bounded_file_sources(
    sources: list[dict], query: str, *, max_total_chars: int = 24_000, window_chars: int = 4_000,
    priority_file_ids: list[str] | None = None, full_if_fits: bool = False,
) -> list[dict]:
    """Select an evidence window per document without altering the stored text."""
    terms = [term.casefold() for term in re.findall(r'\w+', query) if len(term) > 2]
    if priority_file_ids:
        priority = {str(file_id) for file_id in priority_file_ids}
        sources = sorted(
            sources,
            key=lambda source: str((source.get('source') or {}).get('id')) not in priority,
        )
    if full_if_fits:
        total_chars = sum(
            len(doc) for source in sources for doc in source.get('document', []) if isinstance(doc, str)
        )
        complete_coverage = bool(sources) and all(
            len(source.get('document', [])) == len(source.get('metadata', []))
            and bool(source.get('metadata'))
            and all(meta.get('coverage_complete') is True for meta in source['metadata'])
            for source in sources
        )
        if complete_coverage and total_chars <= max_total_chars:
            complete = []
            for source in sources:
                metadata = []
                for doc, meta in zip(source.get('document', []), source.get('metadata', [])):
                    if not isinstance(doc, str):
                        continue
                    file_id = str((source.get('source') or {}).get('id') or (meta or {}).get('file_id') or '')
                    start = int((meta or {}).get('start_offset', 0))
                    end = int((meta or {}).get('end_offset', start + len(doc)))
                    evidence_id = (meta or {}).get('evidence_id') or hashlib.sha256(
                        f'{file_id}:{start}:{end}:{len(doc)}'.encode()
                    ).hexdigest()[:24]
                    metadata.append({
                        **(meta or {}), 'evidence_id': evidence_id,
                        'start_char': start, 'end_char': end,
                        'total_chars': (meta or {}).get('total_chars', end), 'truncated': False,
                    })
                if metadata:
                    complete.append({**source, 'metadata': metadata})
            return complete
    total_documents = sum(min(len(source.get('document', [])), len(source.get('metadata', []))) for source in sources)
    fair_size = max(1, min(window_chars, max_total_chars // max(total_documents, 1)))
    remaining = max_total_chars
    selected = []
    for source in sources:
        documents = []
        metadatas = []
        for doc, meta in zip(source.get('document', []), source.get('metadata', [])):
            if not isinstance(doc, str) or remaining <= 0:
                continue
            lower = doc.casefold()
            match_at = min((index for term in terms if (index := lower.find(term)) >= 0), default=0)
            size = min(fair_size, remaining)
            start = max(0, min(match_at - size // 3, len(doc) - size))
            end = min(len(doc), start + size)
            file_id = str((source.get('source') or {}).get('id') or (meta or {}).get('file_id') or '')
            source_start = int((meta or {}).get('start_offset', 0))
            evidence_id = (meta or {}).get('evidence_id') or hashlib.sha256(
                f'{file_id}:{(meta or {}).get("revision")}:{source_start}:{start}:{end}:{doc}'.encode()
            ).hexdigest()[:24]
            documents.append(doc[start:end])
            metadatas.append({
                **(meta or {}),
                'evidence_id': evidence_id,
                'start_char': source_start + start,
                'end_char': source_start + end,
                'total_chars': (meta or {}).get('total_chars', source_start + len(doc)),
                'truncated': start > 0 or end < len(doc),
            })
            remaining -= end - start
        if documents:
            selected.append({**source, 'document': documents, 'metadata': metadatas})
    return selected


def bounded_tool_output(content: str, *, max_chars: int = 12_000) -> str | dict:
    """Refuse oversized observations rather than silently omitting evidence."""
    if len(content) <= max_chars:
        return content
    return {
        'code': 'tool_result_too_large',
        'original_chars': len(content),
        'limit_chars': max_chars,
        'message': 'Tool output is too large. Request a narrower page or range.',
    }
