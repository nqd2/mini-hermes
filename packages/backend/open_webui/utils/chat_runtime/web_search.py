"""Web-search query planning, execution and event reporting for chat turns."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from starlette.responses import JSONResponse

from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import get_last_user_message

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebSearchAdapters:
    """Existing Open WebUI query, retrieval and form dependencies."""

    generate_queries: Callable[..., Awaitable[Any]]
    process_web_search: Callable[..., Awaitable[Any]]
    search_form_factory: Callable[[list[str]], Any]
    queries_cache_enabled: bool


async def run_chat_web_search(request, form_data, extra_params, user, adapters: WebSearchAdapters):
    event_emitter = extra_params['__event_emitter__']
    await _emit_search_status(
        event_emitter,
        {
            'action': 'web_search',
            'description': 'Searching the web',
            'done': False,
        },
    )

    messages = form_data['messages']
    user_message = get_last_user_message(messages)
    queries = await _generate_search_queries(request, form_data, extra_params, user, user_message, adapters)

    if len(queries) == 1 and queries[0].strip() == '':
        queries = [user_message or '']

    if len(queries) == 0:
        await _emit_search_status(
            event_emitter,
            {
                'action': 'web_search',
                'description': 'No search query generated',
                'done': True,
            },
        )
        return form_data

    await _emit_search_status(
        event_emitter,
        {
            'action': 'web_search_queries_generated',
            'queries': queries,
            'done': False,
        },
    )
    await _search_and_attach_results(request, form_data, user, queries, event_emitter, adapters)
    return form_data


async def _generate_search_queries(request, form_data, extra_params, user, user_message, adapters):
    messages = form_data['messages']
    try:
        res = await adapters.generate_queries(
            request,
            {
                'model': form_data['model'],
                'messages': messages,
                'prompt': user_message,
                'type': 'web_search',
                'chat_id': extra_params.get('__chat_id__'),
            },
            user,
        )

        # Query generation can return an error response instead of raising.
        # Re-raise it so the outer handler falls back to the raw user message.
        if isinstance(res, JSONResponse):
            try:
                error_body = JSONCodec.loads(res.body)
                detail = error_body.get('detail', 'Query generation failed')
            except Exception:
                detail = 'Query generation failed'
            raise Exception(detail)

        response = res['choices'][0]['message']['content']
        try:
            bracket_start = response.rfind('{')
            bracket_end = response.rfind('}') + 1
            if bracket_start == -1 or bracket_end == -1:
                raise Exception('No JSON object found in the response')
            response = response[bracket_start:bracket_end]
            queries = JSONCodec.loads(response)
            queries = queries.get('queries', [])
        except Exception:
            queries = [response]

        if adapters.queries_cache_enabled:
            request.state.cached_queries = queries
    except Exception as e:
        log.exception(e)
        return [user_message or '']
    return queries


async def _search_and_attach_results(request, form_data, user, queries, event_emitter, adapters):
    try:
        results = await adapters.process_web_search(
            request,
            adapters.search_form_factory(queries),
            user=user,
        )

        if results:
            files = form_data.get('files', [])
            if results.get('collection_names'):
                for collection_name in results.get('collection_names'):
                    files.append(
                        {
                            'collection_name': collection_name,
                            'name': ', '.join(queries),
                            'type': 'web_search',
                            'urls': results['filenames'],
                            'queries': queries,
                        }
                    )
            elif results.get('docs'):
                files.append(
                    {
                        'docs': results['docs'],
                        'name': ', '.join(queries),
                        'type': 'web_search',
                        'urls': results['filenames'],
                        'queries': queries,
                    }
                )

            form_data['files'] = files
            await _emit_search_status(
                event_emitter,
                {
                    'action': 'web_search',
                    'description': 'Searched {{count}} sites',
                    'urls': results['filenames'],
                    'items': results.get('items', []),
                    'done': True,
                },
            )
        else:
            await _emit_search_status(
                event_emitter,
                {
                    'action': 'web_search',
                    'description': 'No search results found',
                    'done': True,
                    'error': True,
                },
            )
    except Exception as e:
        log.exception(e)
        detail = e.detail if isinstance(e, HTTPException) else None
        await _emit_search_status(
            event_emitter,
            {
                'action': 'web_search',
                'description': str(detail) if detail else 'An error occurred while searching the web',
                'queries': queries,
                'done': True,
                'error': True,
            },
        )


async def _emit_search_status(event_emitter, data):
    await event_emitter({'type': 'status', 'data': data})
