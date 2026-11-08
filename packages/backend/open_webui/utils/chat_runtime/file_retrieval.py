"""Legacy chat file retrieval adapter used for model knowledge and File Context."""

import logging

from fastapi import Request

from open_webui.models.config import Config
from open_webui.models.users import UserModel
from open_webui.retrieval.utils import get_sources_from_items
from open_webui.routers.tasks import generate_queries
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import get_last_user_message

log = logging.getLogger(__name__)


async def _generate_retrieval_queries(request: Request, body: dict, user: UserModel, event_emitter) -> list:
    queries = []
    try:
        response = await generate_queries(
            request,
            {
                'model': body['model'],
                'messages': body['messages'],
                'type': 'retrieval',
                'chat_id': body.get('metadata', {}).get('chat_id'),
            },
            user,
        )
        response = response['choices'][0]['message']['content']

        try:
            bracket_start = response.rfind('{')
            bracket_end = response.rfind('}') + 1

            if bracket_start == -1 or bracket_end == -1:
                raise Exception('No JSON object found in the response')

            response = response[bracket_start:bracket_end]
            response = JSONCodec.loads(response)
        except Exception:
            response = {'queries': [response]}

        queries = response.get('queries', [])
    except Exception:
        pass

    await event_emitter(
        {
            'type': 'status',
            'data': {
                'action': 'queries_generated',
                'queries': queries,
                'done': False,
            },
        }
    )
    return queries


async def _retrieve_file_sources(
    request: Request, files: list[dict], queries: list, user: UserModel, all_full_context: bool
):
    try:
        rag_config = await Config.get_many(
            'rag.top_k',
            'rag.top_k_reranker',
            'rag.relevance_threshold',
            'rag.hybrid_bm25_weight',
            'rag.enable_hybrid_search',
            'rag.full_context',
        )
        return await get_sources_from_items(
            request=request,
            items=files,
            queries=queries,
            embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                query, prefix=prefix, user=user
            ),
            k=rag_config.get('rag.top_k'),
            reranking_function=(
                (lambda query, documents: request.app.state.RERANKING_FUNCTION(query, documents, user=user))
                if request.app.state.RERANKING_FUNCTION
                else None
            ),
            k_reranker=rag_config.get('rag.top_k_reranker'),
            r=rag_config.get('rag.relevance_threshold'),
            hybrid_bm25_weight=rag_config.get('rag.hybrid_bm25_weight'),
            hybrid_search=rag_config.get('rag.enable_hybrid_search'),
            full_context=all_full_context or rag_config.get('rag.full_context'),
            user=user,
        )
    except Exception as error:
        log.exception(error)
        return []


def _count_source_ids(sources: list[dict] | None) -> int:
    unique_ids = set()
    for source in sources or []:
        if not source or len(source.keys()) == 0:
            continue

        documents = source.get('document') or []
        metadatas = source.get('metadata') or []
        source_info = source.get('source') or {}

        for index, _ in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else None
            source_id = (metadata or {}).get('source') or (source_info or {}).get('id') or 'N/A'
            unique_ids.add(source_id)

    return len(unique_ids)


async def chat_completion_files_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    """Retrieve legacy attached files and report the sources to the chat event stream."""
    event_emitter = extra_params['__event_emitter__']
    sources = []

    files = [item for item in (body.get('metadata', {}).get('files', None) or []) if item.get('type') != 'filesystem']
    if files:
        all_full_context = all(item.get('context') == 'full' for item in files)

        queries = [] if all_full_context else await _generate_retrieval_queries(request, body, user, event_emitter)

        if len(queries) == 0:
            queries = [get_last_user_message(body['messages']) or '']

        sources = await _retrieve_file_sources(request, files, queries, user, all_full_context)

        log.debug('File retrieval completed: source_records=%d', len(sources or []))
        await event_emitter(
            {
                'type': 'status',
                'data': {
                    'action': 'sources_retrieved',
                    'count': _count_source_ids(sources),
                    'done': True,
                },
            }
        )

    return body, {'sources': sources}
