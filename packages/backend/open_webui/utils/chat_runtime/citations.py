"""Extracted citations adapter; legacy imports are re-exported by middleware."""

from __future__ import annotations

from fastapi import Request
from open_webui.env import RAG_SYSTEM_CONTEXT
from open_webui.models.config import Config
from open_webui.retrieval.utils import filter_source_metadata
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import add_or_update_system_message, add_or_update_user_message
from open_webui.utils.task import rag_template
import html
import logging


log = logging.getLogger(__name__)

CITATION_TOOL_NAMES = (
    'fetch_url', 'view_file', 'view_knowledge_file', 'grep_chat_files', 'query_knowledge_files', 'query_chat_files',
    'read_editable_document', 'search_editable_document', 'get_document_task',
)
SOURCE_PROVENANCE_METADATA_KEYS = (
    'evidence_id', 'revision', 'start_offset', 'end_offset', 'start_char', 'end_char',
    'page', 'section', 'sheet', 'line', 'first_row', 'last_row', 'total_chars',
    'coverage_complete', 'truncated',
)
MAX_SOURCE_EVIDENCE_IDS = 32

def get_citation_source_from_tool_result(
    tool_name: str, tool_params: dict, tool_result: str, tool_id: str = ''
) -> list[dict]:
    """
    Parse a tool's result and convert it to source dicts for citation display.

    Follows the source format conventions from get_sources_from_items:
    - source: file/item info object with id, name, type
    - document: list of document contents
    - metadata: list of metadata objects with source, file_id, name fields

    Returns a list of sources (usually one, but query_knowledge_files/query_chat_files may return multiple).
    """
    try:
        try:
            tool_result = JSONCodec.loads(tool_result)
        except (JSONCodec.JSONDecodeError, TypeError):
            pass  # keep tool_result as-is (e.g. fetch_url returns plain text)
        if isinstance(tool_result, dict) and 'error' in tool_result:
            return []

        if tool_name in ('read_editable_document', 'search_editable_document'):
            if not isinstance(tool_result, dict):
                return []
            evidence = []
            if tool_name == 'read_editable_document':
                evidence = [{
                    'evidence_id': tool_result.get('evidence_id'),
                    'text': tool_result.get('text', ''),
                    'start_offset': tool_result.get('offset'),
                    'end_offset': tool_result.get('next_offset') or tool_result.get('total_characters'),
                }]
            else:
                evidence = tool_result.get('results') or []
            return [
                {
                    'source': {
                        'id': tool_result.get('document_id', ''),
                        'name': tool_result.get('filename', 'Document'),
                        'type': 'file',
                    },
                    'document': [item.get('text', '')],
                    'metadata': [{
                        'file_id': tool_result.get('document_id', ''),
                        'name': tool_result.get('filename', 'Document'),
                        'source': tool_result.get('filename', 'Document'),
                        'revision': tool_result.get('revision'),
                        'evidence_id': item.get('evidence_id'),
                        'section': item.get('section'),
                        'page': item.get('page'),
                        'start_offset': item.get('start_offset'),
                        'end_offset': item.get('end_offset'),
                        **(item.get('metadata') or {}),
                    }],
                }
                for item in evidence if item.get('evidence_id') and item.get('text')
            ]

        if tool_name == 'get_document_task' and isinstance(tool_result, dict):
            summary = tool_result.get('result')
            if not summary:
                return []
            return [{
                'source': {'id': tool_result.get('document_id', ''),
                           'name': tool_result.get('filename', 'Document'), 'type': 'file'},
                'document': [summary],
                'metadata': [{
                    'file_id': tool_result.get('document_id', ''),
                    'name': tool_result.get('filename', 'Document'),
                    'source': tool_result.get('filename', 'Document'),
                    'revision': tool_result.get('revision'),
                    'evidence_ids': tool_result.get('evidence_ids', []),
                    'coverage': 'hierarchical summary',
                }],
            }]

        if tool_name in ('view_knowledge_file', 'view_file'):
            if not isinstance(tool_result, dict):
                return []

            file_data = tool_result
            filename = file_data.get('filename', 'Unknown File')
            file_id = file_data.get('id', '')
            knowledge_name = file_data.get('knowledge_name', '')
            content = file_data.get('content', '')
            evidence_id = file_data.get('evidence_id')
            if not content or not evidence_id:
                return []

            return [
                {
                    'source': {
                        'id': file_id,
                        'name': filename,
                        'type': 'file',
                    },
                    'document': [content],
                    'metadata': [
                        {
                            'file_id': file_id,
                            'name': filename,
                            'source': filename,
                            'revision': file_data.get('revision'),
                            'evidence_id': evidence_id,
                            'start_offset': file_data.get('start_offset'),
                            'end_offset': file_data.get('end_offset'),
                            **({'knowledge_name': knowledge_name} if knowledge_name else {}),
                        }
                    ],
                }
            ]

        if tool_name == 'grep_chat_files' and isinstance(tool_result, dict):
            return [
                {
                    'source': {
                        'id': item.get('file_id', ''),
                        'name': item.get('filename', 'Unknown file'),
                        'type': 'file',
                    },
                    'document': [item['text']],
                    'metadata': [{
                        'file_id': item.get('file_id', ''),
                        'name': item.get('filename', 'Unknown file'),
                        'source': item.get('filename', 'Unknown file'),
                        'revision': item.get('revision'),
                        'evidence_id': item.get('evidence_id'),
                        'line': item.get('line_number'),
                        'start_offset': item.get('start_offset'),
                        'end_offset': item.get('end_offset'),
                        'truncated': item.get('truncated', False),
                    }],
                }
                for item in tool_result.get('results', [])
                if item.get('text') and item.get('evidence_id')
            ]

        elif tool_name == 'fetch_url':
            url = tool_params.get('url', '')
            content = tool_result if isinstance(tool_result, str) else str(tool_result)
            snippet = content[:500] + ('...' if len(content) > 500 else '')

            return [
                {
                    'source': {'name': url or 'fetch_url', 'id': url or 'fetch_url'},
                    'document': [snippet],
                    'metadata': [
                        {
                            'source': url,
                            'name': url,
                            'url': url,
                        }
                    ],
                }
            ]

        elif tool_name in ('query_knowledge_files', 'query_chat_files'):
            if not isinstance(tool_result, list):
                return []

            chunks = tool_result

            # Group chunks by source for better citation display
            # Each unique source becomes a separate source entry
            sources_by_file = {}

            for chunk in chunks:
                source_name = chunk.get('source', 'Unknown')
                file_id = chunk.get('file_id', '')
                note_id = chunk.get('note_id', '')
                chunk_type = chunk.get('type', 'file')
                content = chunk.get('content', '')

                # Use file_id or note_id as the key
                key = file_id or note_id or source_name

                if key not in sources_by_file:
                    sources_by_file[key] = {
                        'source': {
                            'id': file_id or note_id,
                            'name': source_name,
                            'type': chunk_type,
                        },
                        'document': [],
                        'metadata': [],
                        'distances': [],
                    }

                sources_by_file[key]['document'].append(content)
                sources_by_file[key]['metadata'].append(
                    {
                        'file_id': file_id,
                        'name': source_name,
                        'source': source_name,
                        **{
                            field: chunk[field] for field in (
                                'revision', 'evidence_id', 'start_char', 'end_char',
                                'start_offset', 'end_offset', 'page', 'section', 'sheet',
                            ) if field in chunk
                        },
                        **({'note_id': note_id} if note_id else {}),
                    }
                )
                if 'distance' in chunk:
                    sources_by_file[key]['distances'].append(chunk['distance'])

            # Return all grouped sources as a list
            if sources_by_file:
                return list(sources_by_file.values())

            # Empty result fallback
            return []

        else:
            # Fallback for other tools
            return [
                {
                    'source': {
                        'name': tool_name,
                        'type': 'tool',
                        'id': tool_id or tool_name,
                    },
                    'document': [str(tool_result)],
                    'metadata': [{'source': tool_name, 'name': tool_name}],
                }
            ]
    except Exception as e:
        log.exception(f'Error parsing tool result for {tool_name}: {e}')
        return [
            {
                'source': {'name': tool_name, 'type': 'tool'},
                'document': [str(tool_result)],
                'metadata': [{'source': tool_name}],
            }
        ]

def get_source_context(sources: list, source_ids: dict = None, include_content: bool = True) -> str:
    """
    Build <source> tag context string from citation sources.
    """
    context_string = ''
    if source_ids is None:
        source_ids = {}
    for source in sources:
        for doc, meta in zip(source.get('document', []), source.get('metadata', [])):
            source_id = meta.get('source') or source.get('source', {}).get('id') or 'N/A'
            if source_id not in source_ids:
                source_ids[source_id] = len(source_ids) + 1
            src_name = source.get('source', {}).get('name')
            src_type = source.get('source', {}).get('type')
            src_rid = source.get('source', {}).get('id') if src_type != 'text' else None
            body = doc if include_content else ''
            extra_attrs = ''
            visible_metadata = filter_source_metadata(meta)
            for key in SOURCE_PROVENANCE_METADATA_KEYS:
                if meta.get(key) is not None:
                    visible_metadata.setdefault(key, meta[key])
            evidence_ids = meta.get('evidence_ids')
            if isinstance(evidence_ids, list):
                bounded_evidence_ids = [str(item) for item in evidence_ids[:MAX_SOURCE_EVIDENCE_IDS]]
                if bounded_evidence_ids:
                    visible_metadata.setdefault('evidence_ids', ','.join(bounded_evidence_ids))
                if len(evidence_ids) > MAX_SOURCE_EVIDENCE_IDS:
                    visible_metadata.setdefault('evidence_ids_truncated', True)
            for key, value in visible_metadata.items():
                if key in ('id', 'name', 'resource-type', 'resource-id'):
                    continue
                extra_attrs += f' {key}="{html.escape(str(value))}"'
            context_string += (
                f'<source id="{source_ids[source_id]}"'
                + (f' name="{src_name}"' if src_name else '')
                + (f' resource-type="{src_type}"' if src_type else '')
                + (f' resource-id="{src_rid}"' if src_rid else '')
                + extra_attrs
                + f'>{body}</source>\n'
            )
    return context_string

async def apply_source_context_to_messages(
    request: Request,
    messages: list,
    sources: list,
    user_message: str,
    include_content: bool = True,
) -> list:
    """
    Build source context from citation sources and apply to messages.
    Uses RAG template to format context for model consumption.

    When include_content is False, emit <source> tags with id/name but no
    document body — useful when the content is already present elsewhere
    (e.g. in a tool result message) and only citation markers are needed.
    """
    if not sources or not user_message:
        return messages

    context = get_source_context(sources, include_content=include_content)

    context = context.strip()
    if not context:
        return messages

    if RAG_SYSTEM_CONTEXT:
        return add_or_update_system_message(
            await rag_template(await Config.get('rag.template'), context, user_message),
            messages,
            append=True,
        )
    else:
        return add_or_update_user_message(
            await rag_template(await Config.get('rag.template'), context, user_message),
            messages,
            append=False,
        )
