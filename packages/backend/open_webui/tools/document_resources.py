"""Tools for discovering, searching, and reading files attached to a chat."""

import asyncio
import logging

from fastapi import Request

from open_webui.env import KNOWLEDGE_GREP_MAX_MATCHES
from open_webui.mini_hermes.provenance import content_version, evidence_id
from open_webui.models.config import Config
from open_webui.models.users import UserModel
from open_webui.retrieval.utils import filter_source_metadata
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)


async def _has_read_access_to_file(
    file,
    user: dict,
    model_knowledge: list[dict] | None = None,
) -> bool:
    """Check if a user can read a file via ownership, admin role, model attachment, or access grants."""
    user_id = user.get('id')
    user_role = user.get('role', 'user')
    if file.user_id == user_id or user_role == 'admin':
        return True
    if model_knowledge and any(item.get('type') == 'file' and item.get('id') == file.id for item in model_knowledge):
        return True
    from open_webui.utils.access_control.files import has_access_to_file

    return await has_access_to_file(
        file_id=file.id,
        access_type='read',
        user=UserModel(**user),
    )


async def _get_accessible_chat_files(
    files: list[dict] | None,
    user: dict,
    file_id: str | None = None,
) -> list[tuple[dict, object]]:
    from open_webui.models.files import Files

    accessible = []
    seen = set()

    for item in files or []:
        if not isinstance(item, dict) or item.get('type', 'file') != 'file':
            continue
        fid = item.get('id') or item.get('url') or ''
        if (
            not isinstance(fid, str)
            or not fid
            or fid in seen
            or fid.startswith(('http://', 'https://', 'data:'))
            or (file_id and fid != file_id)
        ):
            continue
        normalized = {**item, 'id': fid, 'type': 'file'}
        if 'name' not in normalized and item.get('filename'):
            normalized['name'] = item.get('filename')
        seen.add(fid)

        file = await Files.get_file_by_id(fid)
        if file and await _has_read_access_to_file(file, user):
            accessible.append((normalized, file))

    return accessible


def _chat_file_scope_error(
    files: list[dict] | None,
    file_id: str | None,
    resource_scope_ids: list[str] | None,
) -> str | None:
    attached_ids = {
        file_ref
        for item in files or []
        if isinstance(item, dict) and item.get('type', 'file') == 'file'
        if isinstance(file_ref := (item.get('id') or item.get('url')), str)
        and file_ref
        and not file_ref.startswith(('http://', 'https://', 'data:'))
    }
    if not attached_ids:
        return 'No files are attached to this chat'
    if resource_scope_ids is not None:
        attached_ids.intersection_update(str(value) for value in resource_scope_ids)
    if file_id and file_id not in attached_ids:
        return 'File not found'
    return None


async def _get_scoped_accessible_chat_files(
    files: list[dict] | None,
    user: dict,
    file_id: str | None,
    resource_scope_ids: list[str] | None,
) -> list[tuple[dict, object]]:
    accessible = await _get_accessible_chat_files(files, user, file_id)
    return _filter_resource_scope(accessible, resource_scope_ids)


def _filter_resource_scope(files: list[tuple[dict, object]], resource_scope_ids: list[str] | None):
    if resource_scope_ids is None:
        return files
    allowed = {str(file_id) for file_id in resource_scope_ids}
    return [(item, file) for item, file in files if str(item.get('id') or file.id) in allowed]


def _grep_chat_file_models(
    files_to_search: list,
    pattern: str,
    case_insensitive: bool = False,
    count_only: bool = False,
) -> str:
    """Search the full scoped set and return bounded, versioned evidence."""
    from open_webui.tools.knowledge_fs import build_span_matcher

    spans, error = build_span_matcher(pattern, case_insensitive)
    if error:
        return JSONCodec.dumps({'error': error})

    max_matches = KNOWLEDGE_GREP_MAX_MATCHES
    results = []
    evidence_ids = []
    total_matches = 0
    files_searched = []
    truncated = False
    try:
        for file in files_to_search:
            files_searched.append(file.id)
            content = file.data.get('content', '') if file.data else ''
            version = content_version(content)
            file_matches = 0
            offset = 0
            line_number = 1
            for raw_line in content.splitlines(keepends=True):
                line = raw_line.rstrip('\r\n')
                for match_start, match_end in spans(line):
                    total_matches += 1
                    file_matches += 1
                    if not count_only and len(results) < max_matches:
                        excerpt_start = max(0, match_start - 600)
                        excerpt_end = min(len(line), match_end + 600)
                        excerpt = line[excerpt_start:excerpt_end]
                        start = offset + excerpt_start
                        end = offset + excerpt_end
                        identifier = evidence_id(file.id, version, start, end, excerpt)
                        results.append(
                            {
                                'file_id': file.id,
                                'filename': file.filename,
                                'line_number': line_number,
                                'start_offset': start,
                                'end_offset': end,
                                'revision': version,
                                'evidence_id': identifier,
                                'text': excerpt,
                                'truncated': excerpt_start > 0 or excerpt_end < len(line),
                            }
                        )
                        evidence_ids.append(identifier)
                        truncated = truncated or excerpt_start > 0 or excerpt_end < len(line)
                offset += len(raw_line)
                line_number += 1
            if count_only and file_matches:
                results.append({'file_id': file.id, 'filename': file.filename, 'match_count': file_matches})
    except Exception as error:
        return JSONCodec.dumps({'error': str(error)})

    truncated = truncated or (not count_only and total_matches > max_matches)
    return JSONCodec.dumps(
        {
            'status': 'succeeded',
            'pattern': pattern,
            'results': results,
            'evidence_ids': evidence_ids,
            'searched_file_ids': files_searched,
            'total_matches': total_matches,
            'coverage_complete': True,
            'truncated': truncated,
        },
        ensure_ascii=False,
    )


async def list_chat_files(
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __resource_scope_ids__: list[str] = None,
) -> str:
    """
    List files attached to the current chat.

    :return: JSON with attached chat files containing id, filename, content type, size, and updated time when available
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        files = []
        accessible = await _get_accessible_chat_files(__files__, __user__)
        accessible = _filter_resource_scope(accessible, __resource_scope_ids__)
        for item, file in accessible:
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'name': item.get('name') or file.filename,
                'type': item.get('type', 'file'),
                'updated_at': file.updated_at,
            }
            content_type = item.get('content_type') or (file.meta or {}).get('content_type')
            size = item.get('size') or (file.meta or {}).get('size')
            if content_type:
                file_info['content_type'] = content_type
            if size:
                file_info['size'] = size
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def grep_chat_files(
    pattern: str,
    file_id: str | None = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __resource_scope_ids__: list[str] = None,
) -> str:
    """
    Search exact text across files attached to the current chat.
    Pass file_id from the attached_files block to search one file.
    Auto-detected regex uses RE2 syntax; no lookarounds/backreferences, and shorthand classes are ASCII-only.

    :param pattern: The text pattern to search for
    :param file_id: Optional attached file ID to search within a single file
    :param case_insensitive: If true, ignore case when matching
    :param count_only: If true, return only match counts per file
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    file_id = _normalize_file_id(file_id)

    try:
        scope_error = _chat_file_scope_error(__files__, file_id, __resource_scope_ids__)
        if scope_error:
            return JSONCodec.dumps({'error': scope_error})

        accessible = await _get_scoped_accessible_chat_files(__files__, __user__, file_id, __resource_scope_ids__)
        files_to_search = [file for _, file in accessible]
        if not accessible:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(_grep_chat_file_models, files_to_search, pattern, case_insensitive, count_only)
    except Exception as e:
        log.exception(f'grep_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_chat_files(
    query: str,
    file_id: str | None = None,
    count: int | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __resource_scope_ids__: list[str] = None,
) -> str:
    """
    Search files attached to the current chat using semantic/vector search.
    Pass file_id from the attached_files block to search one file, or omit it to search all attached chat files.

    :param query: The search query to find semantically relevant content
    :param file_id: Optional attached file ID to search within a single file
    :param count: Maximum number of results to return, capped by the server RAG top k
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    file_id = _normalize_file_id(file_id)
    count = _normalize_result_count(count)

    try:
        scope_error = _chat_file_scope_error(__files__, file_id, __resource_scope_ids__)
        if scope_error:
            return JSONCodec.dumps({'error': scope_error})

        accessible = await _get_scoped_accessible_chat_files(
            __files__,
            __user__,
            file_id,
            __resource_scope_ids__,
        )
        if not accessible:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await _query_chat_file_content(query, __request__, __user__, accessible, count)
    except Exception as e:
        log.exception(f'query_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


def _normalize_file_id(file_id: str | None) -> str | None:
    return None if isinstance(file_id, str) and file_id.lower() in ('none', 'null', '') else file_id


def _normalize_result_count(count: int | str | None) -> int | None:
    if not isinstance(count, str):
        return count
    if count.lower() in ('none', 'null', ''):
        return None
    try:
        return int(count)
    except ValueError:
        return None


async def _query_chat_file_content(
    query: str,
    request: Request,
    user: dict,
    accessible: list[tuple[dict, object]],
    count: int | None,
) -> str:
    from open_webui.retrieval.utils import get_sources_from_items

    file_items = [{**item} for item, _ in accessible]
    rag_config = await Config.get_many(
        'rag.top_k',
        'rag.top_k_reranker',
        'rag.relevance_threshold',
        'rag.hybrid_bm25_weight',
        'rag.enable_hybrid_search',
        'rag.full_context',
    )
    top_k = rag_config.get('rag.top_k') or 5
    result_count = top_k if count is None else max(1, min(count, top_k))
    full_context = all(item.get('context') == 'full' for item in file_items) or rag_config.get('rag.full_context')

    embedding_function = getattr(request.app.state, 'EMBEDDING_FUNCTION', None)
    if not embedding_function and not full_context:
        return JSONCodec.dumps({'error': 'Embedding function not configured'})

    user_model = UserModel(**user)
    sources = await get_sources_from_items(
        request=request,
        items=file_items,
        queries=[query],
        embedding_function=lambda queries, prefix: (
            embedding_function(queries, prefix=prefix, user=user_model) if embedding_function else None
        ),
        k=result_count,
        reranking_function=(
            (lambda q, docs: request.app.state.RERANKING_FUNCTION(q, docs, user=user_model))
            if getattr(request.app.state, 'RERANKING_FUNCTION', None)
            else None
        ),
        k_reranker=rag_config.get('rag.top_k_reranker'),
        r=rag_config.get('rag.relevance_threshold'),
        hybrid_bm25_weight=rag_config.get('rag.hybrid_bm25_weight'),
        hybrid_search=rag_config.get('rag.enable_hybrid_search'),
        full_context=full_context,
        user=user_model,
    )
    file_versions = {str(file.id): content_version((file.data or {}).get('content', '')) for _item, file in accessible}
    chunks = _format_chat_file_sources(sources, file_versions)
    return JSONCodec.dumps(chunks[:result_count], ensure_ascii=False)


def _format_chat_file_sources(sources: list[dict] | None, file_versions: dict[str, str]) -> list[dict]:
    chunks = []
    for source in sources or []:
        documents = source.get('document') or []
        metadatas = source.get('metadata') or []
        distances = source.get('distances') or []
        source_info = source.get('source') or {}
        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
            chunk = {
                **filter_source_metadata(metadata),
                'content': document,
                'source': metadata.get('source', metadata.get('name', source_info.get('name', 'Unknown'))),
                'file_id': metadata.get('file_id', source_info.get('id', '')),
            }
            file_id_value = str(chunk['file_id'] or '')
            revision = metadata.get('revision') or file_versions.get(file_id_value) or content_version(document)
            start = metadata.get('start_char', metadata.get('start_offset'))
            end = metadata.get('end_char', metadata.get('end_offset'))
            identifier = metadata.get('evidence_id') or evidence_id(
                file_id_value,
                str(revision),
                start,
                end,
                document,
            )
            chunk.update({'evidence_id': identifier, 'revision': revision})
            if start is not None:
                chunk['start_offset'] = start
            if end is not None:
                chunk['end_offset'] = end
            if index < len(distances):
                chunk['distance'] = distances[index]
            chunks.append(chunk)
    return chunks
