"""
Built-in tools for Open WebUI.

These tools are automatically available when native function calling is enabled.

IMPORTANT: DO NOT IMPORT THIS MODULE DIRECTLY IN OTHER PARTS OF THE CODEBASE.
"""

import asyncio
import logging
import time
from typing import Literal, Optional
from urllib.parse import unquote

from fastapi import HTTPException, Request

from open_webui.env import (
    KNOWLEDGE_GREP_MAX_MATCHES,
    VIEW_FILE_DEFAULT_MAX_CHARS,
    VIEW_FILE_MAX_CHARS,
)
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.users import UserModel, Users
from open_webui.retrieval.utils import filter_source_metadata, get_content_from_url
from open_webui.routers.images import (
    CreateImageForm,
    EditImageForm,
    image_edits,
    image_generations,
)
from open_webui.routers.retrieval import search_web as _search_web
from open_webui.tools.knowledge_fs import kb_exec  # noqa: F401 — re-exported
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.document_image_consent import has_approved_document_image_choice, is_office_document_context
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.notifications import notify_target
from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.skill_files import SkillFile, SkillFileOperation, skill_content_page, skill_overview
from open_webui.utils.terminals import upload_files_to_terminal
from open_webui.mini_hermes.provenance import content_version, evidence_id
from open_webui.tools.document_resources import (
    _filter_resource_scope,
    _get_accessible_chat_files,
    _grep_chat_file_models,
    _has_read_access_to_file,
    grep_chat_files,
    list_chat_files,
    query_chat_files,
)
from open_webui.tools.memory_resources import (
    add_memory,
    delete_memory,
    list_memories,
    list_memory_paths,
    read_memory_path,
    replace_memory_content,
    search_memories,
    update_memory,
)
from open_webui.tools.time_resources import (  # noqa: F401
    calculate_timestamp,
    get_current_timestamp,
)

log = logging.getLogger(__name__)

MAX_KNOWLEDGE_BASE_SEARCH_ITEMS = 10_000


async def read_agent_artifact(
    artifact_id: str,
    offset: int = 0,
    length: int = 24000,
    __user__: dict | None = None,
) -> str:
    """Read a bounded byte range from a large tool result stored for your account.

    :param artifact_id: Artifact ID shown in a truncated tool result
    :param offset: Zero-based byte offset to continue reading from
    :param length: Number of bytes to read, up to 65536
    :return: The requested text section and the next offset when more content remains
    """
    owner_id = (__user__ or {}).get('id')
    if not owner_id:
        return JSONCodec.dumps({'error': 'User context not available'})
    try:
        from open_webui.agent_runtime.runtime import AgentRuntime

        artifact_store = AgentRuntime.configured().artifact_store
        if artifact_store is None:
            return JSONCodec.dumps({'error': 'Artifact storage is unavailable'})
        artifact = await artifact_store.repository.get_artifact(artifact_id, owner_id=str(owner_id))
        if artifact is None:
            return JSONCodec.dumps({'error': 'Artifact not found'})
        offset = max(0, int(offset))
        length = min(65_536, max(1, int(length)))
        content, start_offset, end_offset, size_bytes = await artifact_store.read_text_range(
            str(owner_id), artifact_id, offset=offset, length=length
        )
        return JSONCodec.dumps(
            {
                'artifact_id': artifact.id,
                'filename': artifact.filename,
                'sha256': artifact.sha256,
                'size_bytes': size_bytes,
                'start_offset': start_offset,
                'end_offset': end_offset,
                'next_offset': end_offset if end_offset < size_bytes else None,
                'content': content,
            },
            ensure_ascii=False,
        )
    except Exception as error:
        log.exception('Unable to read agent artifact %s', artifact_id)
        return JSONCodec.dumps({'error': type(error).__name__})






# Time utilities


async def notify(
    message: str,
    target: str = '',
    title: str = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a notification to the user's configured notification target.

    :param message: Notification body.
    :param target: Optional target id or name. Empty uses the default target.
    :param title: Optional notification title.
    """
    user_id = (__user__ or {}).get('id')
    if not user_id:
        return 'Notification failed: user not found.'

    app_name = getattr(getattr(__request__, 'app', None), 'state', None)
    app_name = getattr(app_name, 'WEBUI_NAME', 'Open WebUI')
    try:
        result = await notify_target(user_id, message, target=target, title=title, app_name=app_name)
        return f'Notification sent to {result.get("target_id")}.'
    except Exception as e:
        return f'Notification failed: {e}'


# Web search tools


async def search_web(
    query: str,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the public web for information. Best for current events, external references,
    or topics not covered in internal documents.

    :param query: The search query to look up
    :param count: Number of results to return (default: admin-configured value)
    :return: JSON with search results containing title, link, and snippet for each result
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        engine = await Config.get('web.search.engine')
        user = UserModel(**__user__) if __user__ else None

        configured = await Config.get('web.search.result_count')
        max_count = 5 if configured is None else configured
        count = max(1, min(count, max_count)) if count is not None else max_count

        results = await _search_web(__request__, engine, query, user)

        # Limit results
        results = results[:count] if results else []

        return JSONCodec.dumps(
            [{'title': r.title, 'link': r.link, 'snippet': r.snippet} for r in results],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_web error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def fetch_url(
    url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Fetch and extract the main text content from a web page URL.

    :param url: The URL to fetch content from
    :return: The extracted text content from the page
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        content, _ = await get_content_from_url(__request__, url)

        # Truncate if configured (WEB_FETCH_MAX_CONTENT_LENGTH)
        # Guard: content may be None if the web loader silently failed
        if content is not None:
            max_length = await Config.get('web.fetch.max_content_length')
            if max_length and max_length > 0 and len(content) > max_length:
                content = content[:max_length] + '\n\n[Content truncated...]'
        else:
            content = ''

        return content
    except Exception as e:
        log.warning(f'fetch_url error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Image generation tools


async def _document_image_generation_allowed(
    metadata: dict | None,
    chat_id: str | None,
    message_id: str | None,
    user_data: dict | None,
) -> bool:
    if not is_office_document_context(metadata) or not is_saved_chat_id(chat_id) or not message_id or not user_data:
        return False
    try:
        user = UserModel(**user_data)
    except Exception:
        return False

    chat = await Chats.get_accessible_chat_by_id(chat_id, user, permission='write')
    if not chat:
        return False
    message = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
    if not message or (message.get('user_id') or chat.user_id) != user.id:
        return False
    return has_approved_document_image_choice(message.get('output'))


async def generate_image(
    prompt: str,
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Generate an image for an Office document after the user explicitly chose it.

    Before calling this tool, call ask_user by itself with the id
    `document_image_generation`. Its first option must be labelled `Generate image` and
    its second option must decline generation. Call this tool only after the user chose
    `Generate image` for the current Office-document turn.

    :param prompt: A detailed description of the image to generate
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Image URLs and, when a terminal is selected, terminal_path for display_file or further processing.
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})
    if not await _document_image_generation_allowed(__metadata__, __chat_id__, __message_id__, __user__):
        return JSONCodec.dumps({'error': 'Document image generation requires a saved user choice.'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_generations(
            request=__request__,
            form_data=CreateImageForm(prompt=prompt, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        await upload_files_to_terminal(
            __request__, user, images, __metadata__ or {}, {'__event_call__': __event_call__}
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': (
                        'The image is already visible in chat. Use terminal_path with terminal tools or '
                        'display_file when available to continue the requested task.'
                    ),
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'generate_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def edit_image(
    prompt: str,
    image_urls: list[str],
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Transform one or more existing images according to a text prompt.
    Supports targeted edits such as adding, removing, replacing, inpainting, extending, or compositing image content.

    :param prompt: A description of the transformation to apply to the provided images
    :param image_urls: Source image URLs to modify or use as composition inputs
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Image URLs and, when a terminal is selected, terminal_path for display_file or further processing.
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_edits(
            request=__request__,
            form_data=EditImageForm(prompt=prompt, image=image_urls, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        await upload_files_to_terminal(
            __request__, user, images, __metadata__ or {}, {'__event_call__': __event_call__}
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': (
                        'The edited image is already visible in chat. Use terminal_path with terminal tools or '
                        'display_file when available to continue the requested task.'
                    ),
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'edit_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# User input tools


async def ask_user(
    questions: list[dict],
    allow_other: bool = True,
    timeout_ms: int = 120_000,
    __event_call__: callable = None,
) -> str:
    """
    Ask the user clarifying questions before continuing.
    Use this when the next step depends on user intent, preference, or a tradeoff that cannot be inferred safely.

    :param questions: 1-3 question objects, each with id, header, question, and 2-3 options. Each option needs label and description.
        List the option you recommend first; the UI labels the first option Recommended.
    :param allow_other: Whether users may enter a free-form answer instead of choosing one of the options
    :param timeout_ms: Legacy display hint. Saved questions remain durable until answered or denied.
    :return: JSON with status and answers keyed by question id
    """
    try:
        if not isinstance(questions, list):
            raise ValueError('ask_user questions must be an array of question objects.')
        if not 1 <= len(questions) <= 3:
            raise ValueError('ask_user requires 1-3 questions.')

        normalized_questions = []
        seen_ids = set()
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError('Each question must be an object.')

            question_id = str(question.get('id') or '').strip()[:64]
            if not question_id:
                raise ValueError('Each question requires a non-empty id.')
            if question_id in seen_ids:
                raise ValueError(f'Duplicate question id: {question_id}')
            seen_ids.add(question_id)

            options = question.get('options')
            if not isinstance(options, list) or not 2 <= len(options) <= 3:
                raise ValueError('Each question requires 2-3 options.')

            normalized_options = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError('Each option must be an object.')

                label = str(option.get('label') or '').strip()[:80]
                description = str(option.get('description') or '').strip()[:240]
                if not label or not description:
                    raise ValueError('Each option requires a label and description.')

                normalized_options.append(
                    {
                        'label': label,
                        'description': description,
                    }
                )

            question_text = str(question.get('question') or '').strip()[:500]
            if not question_text:
                raise ValueError('Each question requires question text.')

            normalized_questions.append(
                {
                    'id': question_id,
                    'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                    'question': question_text,
                    'options': normalized_options,
                    'allow_other': bool(question.get('allow_other', allow_other)),
                }
            )

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
            timeout_ms = 120_000

        if __event_call__ is None:
            return JSONCodec.dumps(
                {
                    'status': 'error',
                    'error': 'User input requires an active browser session with WebSocket connection.',
                },
                ensure_ascii=False,
            )

        output = await __event_call__(
            {
                'type': 'request:user_input',
                'data': {
                    'questions': normalized_questions,
                    'allow_other': allow_other,
                    'timeout_ms': timeout_ms,
                },
            }
        )

        if not isinstance(output, dict):
            return JSONCodec.dumps({'status': 'error', 'error': 'Invalid user input response.'}, ensure_ascii=False)
        if output.get('error'):
            return JSONCodec.dumps({'status': 'error', 'error': output.get('error')}, ensure_ascii=False)
        if output.get('status') == 'cancelled':
            return JSONCodec.dumps({'status': 'cancelled', 'answers': {}}, ensure_ascii=False)

        return JSONCodec.dumps(
            {
                'status': 'answered',
                'answers': output.get('answers', {}),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'ask_user error: {e}')
        return JSONCodec.dumps({'status': 'error', 'error': str(e)}, ensure_ascii=False)


# Code interpreter tools


async def execute_code(
    code: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __event_call__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute Python code in a sandboxed environment and return the output.
    Use this to perform calculations, data analysis, generate visualizations,
    or run any Python code that would help answer the user's question.

    :param code: The Python code to execute
    :return: JSON with stdout, stderr, and result from execution
    """
    from uuid import uuid4

    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        # Sanitize code (strips ANSI codes and markdown fences)
        code = sanitize_code(code)

        # Import blocked modules from config (same as middleware)
        from open_webui.config import CODE_INTERPRETER_BLOCKED_MODULES

        # Add import blocking code if there are blocked modules
        if CODE_INTERPRETER_BLOCKED_MODULES:
            import textwrap

            blocking_code = textwrap.dedent(
                f"""
                import builtins

                BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}

                _real_import = builtins.__import__
                def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                    if name.split('.')[0] in BLOCKED_MODULES:
                        importer_name = globals.get('__name__') if globals else None
                        if importer_name == '__main__':
                            raise ImportError(
                                f"Direct import of module {{name}} is restricted."
                            )
                    return _real_import(name, globals, locals, fromlist, level)

                builtins.__import__ = restricted_import
                """
            )
            code = blocking_code + '\n' + code

        engine = await Config.get('code_interpreter.engine', 'pyodide')
        if engine == 'pyodide':
            # Execute via frontend pyodide using bidirectional event call
            if __event_call__ is None:
                return JSONCodec.dumps(
                    {'error': 'Event call not available. WebSocket connection required for pyodide execution.'}
                )

            output = await __event_call__(
                {
                    'type': 'execute:python',
                    'data': {
                        'id': str(uuid4()),
                        'code': code,
                        'session_id': (__metadata__.get('session_id') if __metadata__ else None),
                        'files': (__metadata__.get('files', []) if __metadata__ else []),
                    },
                }
            )

            # Parse the output - pyodide returns dict with stdout, stderr, result
            if isinstance(output, dict):
                # Handle error responses from event_caller (e.g. session disconnected, timeout)
                if output.get('error') and not output.get('stdout') and not output.get('result'):
                    stderr = output['error']
                    stdout = ''
                    result = ''
                else:
                    stdout = output.get('stdout', '')
                    stderr = output.get('stderr', '')
                    result = output.get('result', '')
            else:
                stdout = ''
                stderr = ''
                result = str(output) if output else ''

        elif engine == 'jupyter':
            from open_webui.utils.code_interpreter import execute_code_jupyter

            jupyter_auth = await Config.get('code_interpreter.jupyter.auth')

            output = await execute_code_jupyter(
                await Config.get('code_interpreter.jupyter.url'),
                code,
                (await Config.get('code_interpreter.jupyter.auth_token') if jupyter_auth == 'token' else None),
                (await Config.get('code_interpreter.jupyter.auth_password') if jupyter_auth == 'password' else None),
                await Config.get('code_interpreter.jupyter.timeout'),
            )

            stdout = output.get('stdout', '')
            stderr = output.get('stderr', '')
            result = output.get('result', '')

        else:
            return JSONCodec.dumps({'error': f'Unknown code interpreter engine: {engine}'})

        # Handle image outputs (base64 encoded) - replace with uploaded URLs
        # Get actual user object for image upload (upload_image requires user.id attribute)
        if __user__ and __user__.get('id'):
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_url_from_base64

            user = await Users.get_user_by_id(__user__['id'])

            # Extract and upload images from stdout
            if stdout and isinstance(stdout, str):
                stdout_lines = stdout.split('\n')
                for idx, line in enumerate(stdout_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            stdout_lines[idx] = f'![Output Image]({image_url})'
                stdout = '\n'.join(stdout_lines)

            # Extract and upload images from result
            if result and isinstance(result, str):
                result_lines = result.split('\n')
                for idx, line in enumerate(result_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            result_lines[idx] = f'![Output Image]({image_url})'
                result = '\n'.join(result_lines)

        response = {
            'status': 'success',
            'stdout': stdout,
            'stderr': stderr,
            'result': result,
        }

        return JSONCodec.dumps(response, ensure_ascii=False)
    except Exception as e:
        log.exception(f'execute_code error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Memory tool implementations are re-exported from memory_resources.py.

# Note tools remain available from this legacy module for registry compatibility.
from open_webui.tools.note_resources import (
    _emit_note_updated as _emit_note_updated,
    _has_write_access_to_note as _has_write_access_to_note,
    replace_note_content as replace_note_content,
    search_notes as search_notes,
    view_note as view_note,
    write_note as write_note,
)










# Chat tools remain available from this legacy module for registry compatibility.
from open_webui.tools.chat_resources import (
    search_chats as search_chats,
    view_chat as view_chat,
)






# Sub-agent tool


async def delegate_task(
    task: str,
    context: str = '',
    file_ids: list[str] | None = None,
    background: bool = False,
    model_id: str | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Delegate focused work to a parallel sub-agent using the current tools.

    :param task: The specific task for the sub-agent to complete
    :param context: Relevant context, decisions, or file paths for the task
    :param file_ids: Attached file IDs the sub-agent needs. Use this for images or files;
        do not put file IDs only in context.
    :param background: Return immediately and continue this chat when the sub-agent finishes
    :param model_id: ID of an available sub-agent model. Omit to use the current model
    :return: Foreground result text, or a JSON dispatch handle for background work
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: sub-agents cannot delegate recursively.'

    from open_webui.utils.subagents import delegate

    return await delegate(
        task,
        context,
        background,
        file_ids=file_ids,
        model_id=model_id,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


async def timer(
    prompt: str,
    at: str,
    cancel_on: list[Literal['chat.read', 'chat.user_message']] | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Set a one-shot timer for this chat.

    :param prompt: The prompt to send back into this chat when the timer fires
    :param at: Relative time like 10s, 5m, 1h, 2d, or a timezone-aware RFC 3339 timestamp
    :param cancel_on: Optional events that cancel the timer before it fires
    :return: JSON status with the scheduled time, or an error string
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: timers cannot be set from internal chats.'

    from open_webui.utils.timers import create_timer

    return await create_timer(
        prompt=prompt,
        at=at,
        cancel_on=cancel_on,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


# Channels tools — keep legacy imports available to the built-in registry.
from open_webui.tools.channel_resources import (
    search_channel_messages as search_channel_messages,
    search_channels as search_channels,
    view_channel_message as view_channel_message,
    view_channel_thread as view_channel_thread,
)


# Knowledge base tools


async def list_knowledge_bases(
    count: int = 10,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's accessible knowledge bases so a relevant internal source
    can be chosen.

    :param count: Maximum number of KBs to return (default: 10)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with KBs containing id, name, description, and file_count
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Knowledges.search_knowledge_bases(
            user_id,
            filter={
                'query': '',
                'user_id': user_id,
                'group_ids': user_group_ids,
            },
            skip=skip,
            limit=count,
        )

        knowledge_bases = []
        for knowledge_base in result.items:
            files = await Knowledges.get_files_by_id(knowledge_base.id)
            file_count = len(files) if files else 0

            knowledge_bases.append(
                {
                    'id': knowledge_base.id,
                    'name': knowledge_base.name,
                    'description': knowledge_base.description or '',
                    'file_count': file_count,
                    'updated_at': knowledge_base.updated_at,
                }
            )

        return JSONCodec.dumps(knowledge_bases, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_knowledge_bases(
    query: str,
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's accessible knowledge bases by name and description to find
    a relevant internal source.

    :param query: The search query to find matching knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching KBs containing id, name, description, and file_count
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Knowledges.search_knowledge_bases(
            user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
            },
            skip=skip,
            limit=count,
        )

        knowledge_bases = []
        for knowledge_base in result.items:
            files = await Knowledges.get_files_by_id(knowledge_base.id)
            file_count = len(files) if files else 0

            knowledge_bases.append(
                {
                    'id': knowledge_base.id,
                    'name': knowledge_base.name,
                    'description': knowledge_base.description or '',
                    'file_count': file_count,
                    'updated_at': knowledge_base.updated_at,
                }
            )

        return JSONCodec.dumps(knowledge_bases, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_knowledge_files(
    query: str,
    knowledge_id: Optional[str] = None,
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    Search files by filename across knowledge bases the user has access to.
    When the model has attached knowledge, searches only within attached KBs and files.
    Helpful when looking for a specific document or file name.

    :param query: The search query to find matching files by filename
    :param knowledge_id: Optional KB id to limit search to a specific knowledge base
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching files containing id, filename, and updated_at
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        # When model has attached knowledge, scope to attached KBs/files only
        if __model_knowledge__:
            attached_kb_ids = set()
            attached_file_ids = set()

            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')
                if item_type == 'collection':
                    attached_kb_ids.add(item_id)
                elif item_type == 'file':
                    attached_file_ids.add(item_id)

            # If knowledge_id specified, verify it's in the attached set
            if knowledge_id:
                if knowledge_id not in attached_kb_ids:
                    return JSONCodec.dumps({'error': f'Knowledge base {knowledge_id} is not attached to this model'})
                attached_kb_ids = {knowledge_id}

            all_files = []

            # Search within attached KBs
            for kb_id in attached_kb_ids:
                knowledge = await Knowledges.get_knowledge_by_id(kb_id)
                if not knowledge:
                    continue

                if not (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    continue

                result = await Knowledges.search_files_by_id(
                    knowledge_id=kb_id,
                    user_id=user_id,
                    filter={'query': query},
                    skip=0,
                    limit=count + skip,
                )

                for file in result.items:
                    all_files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'knowledge_id': knowledge.id,
                            'knowledge_name': knowledge.name,
                            'updated_at': file.updated_at,
                        }
                    )

            # Search within directly attached files (filename match)
            if not knowledge_id and attached_file_ids:
                query_lower = query.lower() if query else ''
                for file_id in attached_file_ids:
                    file = await Files.get_file_by_id(file_id)
                    if file and (not query_lower or query_lower in file.filename.lower()):
                        all_files.append(
                            {
                                'id': file.id,
                                'filename': file.filename,
                                'updated_at': file.updated_at,
                            }
                        )

            # Apply pagination across combined results
            all_files = all_files[skip : skip + count]
            return JSONCodec.dumps(all_files, ensure_ascii=False)

        # No attached knowledge - search all accessible KBs
        if knowledge_id:
            # search_files_by_id does not enforce knowledge_id ownership; mirror the attached-KB check above.
            knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
            if not knowledge or not (
                user_role == 'admin'
                or knowledge.user_id == user_id
                or await AccessGrants.has_access(
                    user_id=user_id,
                    resource_type='knowledge',
                    resource_id=knowledge.id,
                    permission='read',
                    user_group_ids=set(user_group_ids),
                )
            ):
                return JSONCodec.dumps({'error': f'Access denied to knowledge base {knowledge_id}'})

            result = await Knowledges.search_files_by_id(
                knowledge_id=knowledge_id,
                user_id=user_id,
                filter={'query': query},
                skip=skip,
                limit=count,
            )
        else:
            result = await Knowledges.search_knowledge_files(
                filter={
                    'query': query,
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=skip,
                limit=count,
            )

        files = []
        for file in result.items:
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'updated_at': file.updated_at,
            }
            if hasattr(file, 'collection') and file.collection:
                file_info['knowledge_id'] = file.collection.get('id', '')
                file_info['knowledge_name'] = file.collection.get('name', '')
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


def _grep_file_models(
    files_to_search: list,
    pattern: str,
    case_insensitive: bool = False,
    count_only: bool = False,
) -> str:
    from open_webui.tools.knowledge_fs import build_matcher

    matches, err = build_matcher(pattern, case_insensitive)
    if err:
        return JSONCodec.dumps({'error': err})

    results = []
    total_matches = 0
    counts = []

    for file in files_to_search:
        content = ''
        if file.data:
            content = file.data.get('content', '')
        if not content:
            continue

        lines = content.split('\n')
        file_matches = 0

        for i, line in enumerate(lines, 1):
            if matches(line):
                file_matches += 1
                total_matches += 1
                if not count_only and len(results) < KNOWLEDGE_GREP_MAX_MATCHES:
                    results.append(f'{file.id}  {file.filename}:{i}: {line}')

        if file_matches > 0 and count_only:
            counts.append(f'{file.id}  {file.filename}: {file_matches}')

    if count_only:
        if not counts:
            return f'No matches for "{pattern}"'
        return '\n'.join(counts) + f'\n[{total_matches} total matches]'

    if not results:
        return f'No matches for "{pattern}"'

    output = '\n'.join(results)
    if total_matches > KNOWLEDGE_GREP_MAX_MATCHES:
        output += f'\n[{KNOWLEDGE_GREP_MAX_MATCHES} of {total_matches} matches shown — use file_id to narrow]'
    return output


async def grep_knowledge_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    Search for exact text across knowledge files. Returns matching lines with line numbers.
    Unlike query_knowledge_files (semantic/vector search), this performs exact string matching.
    Automatically detects regex patterns (e.g. "error|warn", "version \\d+").
    Regex uses RE2 syntax; no lookarounds/backreferences, and shorthand character classes are ASCII-only.
    Helpful for literal strings, identifiers, error messages, or regex-style searches.

    :param pattern: The text pattern to search for (regex auto-detected)
    :param file_id: Optional file ID to search within a single file only
    :param case_insensitive: If true, ignore case when matching (default: false)
    :param count_only: If true, return only match counts per file (default: false)
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    try:
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        # Collect files to search
        files_to_search = []

        if file_id:
            # Single file mode — verify access
            file = await Files.get_file_by_id(file_id)
            if file:
                if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
                    return JSONCodec.dumps({'error': 'File not found'})
                files_to_search.append(file)
        elif __model_knowledge__:
            # Scoped to model's attached knowledge
            from open_webui.models.access_grants import AccessGrants

            seen_ids = set()
            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')
                if item_type == 'file' and item_id not in seen_ids:
                    file = await Files.get_file_by_id(item_id)
                    if file:
                        files_to_search.append(file)
                        seen_ids.add(item_id)
                elif item_type == 'collection':
                    knowledge = await Knowledges.get_knowledge_by_id(item_id)
                    if not knowledge:
                        continue
                    # Verify user can access this KB
                    if not (
                        user_role == 'admin'
                        or knowledge.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='read',
                            user_group_ids=set(user_group_ids),
                        )
                    ):
                        continue
                    kb_files = await Knowledges.get_files_by_id(item_id)
                    if kb_files:
                        for f in kb_files:
                            if f.id not in seen_ids:
                                files_to_search.append(f)
                                seen_ids.add(f.id)
        else:
            # All accessible knowledge bases — use the same search pattern as list_knowledge_bases
            result = await Knowledges.search_knowledge_bases(
                user_id,
                filter={
                    'query': '',
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=0,
                limit=200,
            )
            seen_ids = set()
            for kb in result.items:
                file_ids = []
                # Get files attached to this KB
                files_from_kb = await Knowledges.get_files_by_id(kb.id)
                if files_from_kb:
                    file_ids = [f.id for f in files_from_kb]
                for fid in file_ids:
                    if fid not in seen_ids:
                        file = await Files.get_file_by_id(fid)
                        if file:
                            files_to_search.append(file)
                            seen_ids.add(fid)

        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(_grep_file_models, files_to_search, pattern, case_insensitive, count_only)

    except Exception as e:
        log.exception(f'grep_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __files__: list[dict] = None,
    __resource_scope_ids__: list[str] = None,
) -> str:
    """
    Get the content of a file by its ID. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0)
    :param max_chars: Maximum characters to return (a server-side hard cap applies)
    :param line_numbers: If true, prefix each line with its 1-indexed line number
    :param start_line: Optional 1-indexed start line (overrides offset/max_chars when set)
    :param end_line: Optional 1-indexed end line (inclusive; reads from line 1 if no start line is given)
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = VIEW_FILE_DEFAULT_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), VIEW_FILE_MAX_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.agent_runtime.files import AgentFileContentStore
        from open_webui.models.files import Files

        file = await Files.get_file_by_id_without_content(file_id)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})

        if __resource_scope_ids__ is not None and str(file.id) not in {
            str(resource_id) for resource_id in __resource_scope_ids__
        }:
            return JSONCodec.dumps({'error': 'File is outside the scope of this task'})

        if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
            return JSONCodec.dumps({'error': 'File not found'})

        file_content_store = AgentFileContentStore.configured()
        reference = await file_content_store.get_reference(file.id, owner_id=file.user_id)
        content = ''
        if reference is None:
            legacy_file = await Files.get_file_by_id(file_id)
            content = (legacy_file.data or {}).get('content', '') if legacy_file else ''
            if isinstance(content, str) and len(content.encode('utf-8')) > file_content_store.inline_threshold_bytes:
                reference = await file_content_store.migrate_file(file.id, owner_id=file.user_id)
        total_chars = reference.char_count if reference else len(content)
        revision = reference.sha256 if reference else content_version(content)

        # Line-based addressing (overrides char-based offset/max_chars)
        if start_line is not None or end_line is not None:
            total_lines = reference.line_count if reference else len(content.split('\n'))
            s = max(1, int(start_line or 1))
            e = min(total_lines, int(end_line) if end_line else s + 99)
            if s > total_lines:
                selected_rows = []
            elif reference:
                selected_rows = await file_content_store.read_lines(reference, s, e, max_chars=max_chars)
            else:
                all_lines = content.split('\n')
                source_start = sum(len(line) + 1 for line in all_lines[: s - 1])
                selected_rows = []
                source_cursor = source_start
                for line_number, line in enumerate(all_lines[s - 1 : e], start=s):
                    selected_rows.append((line_number, line, source_cursor, source_cursor + len(line)))
                    source_cursor += len(line) + 1

            rendered_lines = []
            bounded_rows = []
            remaining_chars = max_chars
            output_truncated = False
            for line_number, line, line_start, line_end in selected_rows:
                prefix = f'{line_number}: '
                rendered = prefix + line
                if len(rendered) > remaining_chars:
                    visible_line = line[: max(0, remaining_chars - len(prefix))]
                    rendered = (prefix + visible_line)[:remaining_chars]
                    bounded_rows.append((line_number, visible_line, line_start, line_start + len(visible_line)))
                    rendered_lines.append(rendered)
                    output_truncated = True
                    break
                bounded_rows.append((line_number, line, line_start, line_end))
                rendered_lines.append(rendered)
                remaining_chars -= len(rendered) + 1
                if remaining_chars <= 0:
                    output_truncated = line_number < e
                    break
            source_start = bounded_rows[0][2] if bounded_rows else 0
            source_end = bounded_rows[-1][3] if bounded_rows else source_start
            source_text = '\n'.join(item[1] for item in bounded_rows)
            sliced = '\n'.join(rendered_lines)
            is_truncated = e < total_lines or output_truncated or len(bounded_rows) < len(selected_rows)
            result = {
                'id': file.id,
                'filename': file.filename,
                'content': sliced,
                'revision': revision,
                'evidence_id': evidence_id(file.id, revision, source_start, source_end, source_text),
                'start_offset': source_start,
                'end_offset': source_end,
                'updated_at': file.updated_at,
                'created_at': file.created_at,
                'total_lines': total_lines,
                'showing_lines': (
                    f'{bounded_rows[0][0]}-{bounded_rows[-1][0]}' if bounded_rows else f'{s}-{e}'
                ),
            }
            if is_truncated:
                result['truncated'] = True
                result['next_start_line'] = (bounded_rows[-1][0] + 1) if bounded_rows else s
            return JSONCodec.dumps(result, ensure_ascii=False)

        if reference:
            sliced, _, start_ln = await file_content_store.read_chars(reference, offset, max_chars)
        else:
            sliced = content[offset : offset + max_chars]
            start_ln = content[:offset].count('\n') + 1
        returned_chars = len(sliced)
        is_truncated = (offset + returned_chars) < total_chars
        source_slice = sliced

        if line_numbers:
            lines = sliced.split('\n')
            sliced = '\n'.join(f'{start_ln + i}: {line}' for i, line in enumerate(lines))

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'revision': revision,
            'evidence_id': evidence_id(file.id, revision, offset, offset + returned_chars, source_slice),
            'start_offset': offset,
            'end_offset': offset + returned_chars,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = returned_chars
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + returned_chars

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_knowledge_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the content of a file from a knowledge base. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0)
    :param max_chars: Maximum characters to return (a server-side hard cap applies)
    :param line_numbers: If true, prefix each line with its 1-indexed line number
    :param start_line: Optional 1-indexed start line (overrides offset/max_chars when set)
    :param end_line: Optional 1-indexed end line (inclusive; reads from line 1 if no start line is given)
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = VIEW_FILE_DEFAULT_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), VIEW_FILE_MAX_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        file = await Files.get_file_by_id(file_id)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})

        # Check access via any KB containing this file
        knowledges = await Knowledges.get_knowledges_by_file_id(file_id)
        has_knowledge_access = False
        knowledge_info = None

        for knowledge_base in knowledges:
            if (
                user_role == 'admin'
                or knowledge_base.user_id == user_id
                or await AccessGrants.has_access(
                    user_id=user_id,
                    resource_type='knowledge',
                    resource_id=knowledge_base.id,
                    permission='read',
                    user_group_ids=set(user_group_ids),
                )
            ):
                has_knowledge_access = True
                knowledge_info = {'id': knowledge_base.id, 'name': knowledge_base.name}
                break

        if not has_knowledge_access:
            if file.user_id != user_id and user_role != 'admin':
                return JSONCodec.dumps({'error': 'Access denied'})

        content = ''
        if file.data:
            content = file.data.get('content', '')

        total_chars = len(content)

        # Line-based addressing (overrides char-based offset/max_chars)
        if start_line is not None or end_line is not None:
            all_lines = content.split('\n')
            total_lines = len(all_lines)
            s = max(1, int(start_line or 1)) - 1
            e = min(total_lines, int(end_line) if end_line else s + 100)
            selected = all_lines[s:e]
            sliced = '\n'.join(f'{s + i + 1}: {line}' for i, line in enumerate(selected))
            source_text = '\n'.join(selected)
            source_start = sum(len(line) + 1 for line in all_lines[:s])
            source_end = source_start + len(source_text)
            version = content_version(content)
            is_truncated = e < total_lines
            result = {
                'id': file.id,
                'filename': file.filename,
                'content': sliced,
                'revision': version,
                'evidence_id': evidence_id(file.id, version, source_start, source_end, source_text),
                'start_offset': source_start,
                'end_offset': source_end,
                'updated_at': file.updated_at,
                'created_at': file.created_at,
                'total_lines': total_lines,
                'showing_lines': f'{s + 1}-{e}',
            }
            if knowledge_info:
                result['knowledge_id'] = knowledge_info['id']
                result['knowledge_name'] = knowledge_info['name']
            if is_truncated:
                result['truncated'] = True
                result['next_start_line'] = e + 1
            return JSONCodec.dumps(result, ensure_ascii=False)

        sliced = content[offset : offset + max_chars]
        returned_chars = len(sliced)
        is_truncated = (offset + returned_chars) < total_chars
        source_slice = sliced
        version = content_version(content)

        if line_numbers:
            start_ln = content[:offset].count('\n') + 1
            lines = sliced.split('\n')
            sliced = '\n'.join(f'{start_ln + i}: {line}' for i, line in enumerate(lines))

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'revision': version,
            'evidence_id': evidence_id(file.id, version, offset, offset + returned_chars, source_slice),
            'start_offset': offset,
            'end_offset': offset + returned_chars,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }
        if knowledge_info:
            result['knowledge_id'] = knowledge_info['id']
            result['knowledge_name'] = knowledge_info['name']

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = returned_chars
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + returned_chars

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_knowledge_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def display_file(
    path: str,
    page: int | None = None,
    inline: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] | None = None,
    __resource_scope_ids__: list[str] = None,
) -> str:
    """
    Show a terminal, chat, or knowledge base file to the user.
    After display_file succeeds, do not display the same file again or emit Markdown for it.

    :param path: Terminal file path, or file_id or /api/v1/files/<file_id>/content for a chat or knowledge base file
    :param page: For PDF, DOCX, and PPTX files, open the preview at this 1-based page or slide number
    :param inline: Show terminal files inline instead of opening the file viewer; uploaded files always display inline
    :return: JSON with the file's path, name, content type, and display state
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available', 'exists': False})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available', 'exists': False})

    try:
        from open_webui.models.files import Files

        if '/' not in path:
            path = f'/api/v1/files/{path}/content'
        parts = path.split('/')
        if len(parts) != 6 or parts[:4] != ['', 'api', 'v1', 'files'] or parts[-1] != 'content':
            return JSONCodec.dumps(
                {'error': 'Use file_id or /api/v1/files/<file_id>/content for an uploaded file.', 'exists': False}
            )

        file = await Files.get_file_by_id(parts[4])
        if not file:
            return JSONCodec.dumps({'error': 'File not found', 'exists': False})

        if __resource_scope_ids__ is not None and str(file.id) not in {
            str(resource_id) for resource_id in __resource_scope_ids__
        }:
            return JSONCodec.dumps({'error': 'File is outside the scope of this task', 'exists': False})

        if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
            return JSONCodec.dumps({'error': 'File not found', 'exists': False})

        file_meta = file.meta or {}
        result = {
            'type': 'file',
            'source': 'open_webui',
            'exists': True,
            'displayed': True,
            'path': path,
            'full_path': path,
            'name': file_meta.get('name') or file.filename,
            'content_type': file_meta.get('content_type') or 'application/octet-stream',
            'mime_type': file_meta.get('content_type') or 'application/octet-stream',
        }
        if page:
            result['page'] = page
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'display_file error: {e}')
        return JSONCodec.dumps({'error': str(e), 'exists': False})


async def list_knowledge(
    knowledge_id: Optional[str] = None,
    skip: int = 0,
    count: int = 50,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    List knowledge bases, files, and notes attached to the current model.
    Use this first to discover what knowledge is available before querying or reading files.
    Without knowledge_id: returns KB summaries (name, description, file_count)
    plus standalone files and notes — no file listing inside KBs.
    With knowledge_id: includes paginated file listing for that specific KB.
    Use skip/count to page through large KBs.

    :param knowledge_id: Optional KB ID to get file listing for
    :param skip: Number of files to skip for pagination (default: 0)
    :param count: Maximum files per page (default: 50, max: 200)
    :return: JSON with knowledge_bases, files, and notes attached to this model
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not __model_knowledge__:
        return JSONCodec.dumps({'knowledge_bases': [], 'files': [], 'notes': []})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(skip, str):
        try:
            skip = int(skip)
        except ValueError:
            skip = 0
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 50
    if isinstance(knowledge_id, str) and knowledge_id.lower() in ('none', 'null', ''):
        knowledge_id = None

    count = min(count, 200)

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        knowledge_bases = []
        files = []
        notes = []

        for item in __model_knowledge__:
            item_type = item.get('type')
            item_id = item.get('id')

            if item_type == 'collection':
                knowledge = await Knowledges.get_knowledge_by_id(item_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    kb_files = await Knowledges.get_files_by_id(knowledge.id)
                    file_count = len(kb_files) if kb_files else 0

                    kb_entry = {
                        'id': knowledge.id,
                        'name': knowledge.name,
                        'description': knowledge.description or '',
                        'file_count': file_count,
                    }

                    # Include file listing only when this KB is targeted
                    if knowledge_id and knowledge_id == knowledge.id:
                        if kb_files:
                            paged_files = kb_files[skip : skip + count]
                            kb_entry['files'] = [{'id': f.id, 'filename': f.filename} for f in paged_files]
                            kb_entry['files_skip'] = skip
                            kb_entry['files_count'] = len(paged_files)
                            kb_entry['files_total'] = file_count
                            kb_entry['has_more'] = skip + count < file_count

                    knowledge_bases.append(kb_entry)

            elif item_type == 'file':
                file = await Files.get_file_by_id(item_id)
                if file:
                    files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'updated_at': file.updated_at,
                        }
                    )

            elif item_type == 'note':
                note = await Notes.get_note_by_id(item_id)
                if note and (
                    user_role == 'admin'
                    or note.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='note',
                        resource_id=note.id,
                        permission='read',
                    )
                ):
                    notes.append(
                        {
                            'id': note.id,
                            'title': note.title,
                        }
                    )

        return JSONCodec.dumps(
            {
                'knowledge_bases': knowledge_bases,
                'files': files,
                'notes': notes,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_knowledge error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_files(
    query: str,
    knowledge_ids: Optional[list[str]] = None,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] = None,
) -> str:
    """
    Search knowledge base files using semantic/vector search. Searches across collections (KBs),
    individual files, and notes that the user has access to.
    Helpful for internal documentation, uploaded knowledge, and attached model knowledge.

    :param query: The search query to find semantically relevant content
    :param knowledge_ids: Optional list of KB ids to limit search to specific knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 5  # Default fallback

    # Handle knowledge_ids being string "None", "null", or empty
    if isinstance(knowledge_ids, str):
        if knowledge_ids.lower() in ('none', 'null', ''):
            knowledge_ids = None
        else:
            # Try to parse as JSON array if it looks like one
            try:
                knowledge_ids = JSONCodec.loads(knowledge_ids)
            except JSONCodec.JSONDecodeError:
                # Treat as single ID
                knowledge_ids = [knowledge_ids]

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes
        from open_webui.retrieval.external import retrieve_external_knowledge
        from open_webui.retrieval.utils import query_collection

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)

        collection_names = []
        external_knowledges = []
        note_results = []  # Notes aren't vectorized, handle separately

        # If model has attached knowledge, use those
        if __model_knowledge__:
            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')

                if item_type == 'collection':
                    # Knowledge base - use KB ID as collection name
                    knowledge = await Knowledges.get_knowledge_by_id(item_id)
                    if knowledge and (
                        user_role == 'admin'
                        or knowledge.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='read',
                            user_group_ids=set(user_group_ids),
                        )
                    ):
                        if (knowledge.meta or {}).get('source') == 'external':
                            external_knowledges.append(knowledge)
                        else:
                            collection_names.append(item_id)

                elif item_type == 'file':
                    # Individual file - use file-{id} as collection name
                    file = await Files.get_file_by_id(item_id)
                    if file:
                        collection_names.append(f'file-{item_id}')

                elif item_type == 'note':
                    # Note - always return full content as context
                    note = await Notes.get_note_by_id(item_id)
                    if note and (
                        user_role == 'admin'
                        or note.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='note',
                            resource_id=note.id,
                            permission='read',
                        )
                    ):
                        content = note.data.get('content', {}).get('md', '')
                        note_results.append(
                            {
                                'content': content,
                                'source': note.title,
                                'note_id': note.id,
                                'type': 'note',
                            }
                        )

        elif knowledge_ids:
            # User specified specific KBs
            for knowledge_id in knowledge_ids:
                knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    if (knowledge.meta or {}).get('source') == 'external':
                        external_knowledges.append(knowledge)
                    else:
                        collection_names.append(knowledge_id)
        else:
            # No model knowledge and no specific IDs - search all accessible KBs
            result = await Knowledges.search_knowledge_bases(
                user_id,
                filter={
                    'query': '',
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=0,
                limit=50,
            )
            for knowledge_base in result.items:
                if (knowledge_base.meta or {}).get('source') == 'external':
                    external_knowledges.append(knowledge_base)
                else:
                    collection_names.append(knowledge_base.id)

        chunks = []

        # Add note results first
        chunks.extend(note_results)

        # Query vector collections if any
        if collection_names:
            query_results = await query_collection(
                __request__,
                collection_names=collection_names,
                queries=[query],
                embedding_function=lambda queries, prefix: embedding_function(queries, prefix=prefix, user=user_model),
                k=count,
                user=user_model,
            )

            if query_results and 'documents' in query_results:
                documents = query_results.get('documents', [[]])[0]
                metadatas = query_results.get('metadatas', [[]])[0]
                distances = query_results.get('distances', [[]])[0]

                file_ids = {metadata['file_id'] for metadata in metadatas if metadata.get('file_id')}
                if file_ids:
                    file_names = {
                        file.id: (file.meta or {}).get('name')
                        for file in await Files.get_file_metadatas_by_ids(list(file_ids))
                    }
                    for metadata in metadatas:
                        file_name = file_names.get(metadata.get('file_id'))
                        if file_name:
                            if metadata.get('source') == metadata.get('name'):
                                metadata['source'] = file_name
                            metadata['name'] = file_name

                for idx, doc in enumerate(documents):
                    chunk_info = {
                        **filter_source_metadata(metadatas[idx]),
                        'content': doc,
                        'source': metadatas[idx].get('source', metadatas[idx].get('name', 'Unknown')),
                        'file_id': metadatas[idx].get('file_id', ''),
                    }
                    if idx < len(distances):
                        chunk_info['distance'] = distances[idx]
                    chunks.append(chunk_info)

        for knowledge in external_knowledges:
            query_results = await retrieve_external_knowledge(
                __request__,
                knowledge,
                queries=[query],
                count=count,
                user=user_model,
            )
            documents = query_results.get('documents', [[]])[0]
            metadatas = query_results.get('metadatas', [[]])[0]
            distances = query_results.get('distances', [[]])[0]

            for idx, doc in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                chunk_info = {
                    **filter_source_metadata(metadata),
                    'content': doc,
                    'source': metadata.get('source', metadata.get('name', knowledge.name)),
                    'type': 'external',
                    'knowledge_id': knowledge.id,
                }
                if idx < len(distances):
                    chunk_info['distance'] = distances[idx]
                chunks.append(chunk_info)

        # Limit to requested count
        chunks = chunks[:count]

        return JSONCodec.dumps(chunks, ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_bases(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search knowledge bases by semantic similarity to query.
    Finds KBs whose name/description match the meaning of your query.
    Helpful for discovering which knowledge base to query next.

    :param query: Natural language query describing what you're looking for
    :param count: Maximum results (default: 5)
    :return: JSON with matching KBs (id, name, description, similarity)
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        import heapq

        from open_webui.models.knowledge import Knowledges
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
        from open_webui.routers.knowledge import KNOWLEDGE_BASES_COLLECTION

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)
        query_embedding = await embedding_function(
            query, prefix=await Config.get('rag.embedding_query_prefix'), user=user_model
        )

        # Min-heap of (distance, knowledge_base_id) - only holds top `count` results
        top_results_heap = []
        seen_ids = set()
        page_offset = 0
        page_size = 100

        while True:
            accessible_knowledge_bases = await Knowledges.search_knowledge_bases(
                user_id,
                filter={'user_id': user_id, 'group_ids': user_group_ids},
                skip=page_offset,
                limit=page_size,
            )

            if not accessible_knowledge_bases.items:
                break

            accessible_ids = [kb.id for kb in accessible_knowledge_bases.items]

            search_results = await ASYNC_VECTOR_DB_CLIENT.search(
                collection_name=KNOWLEDGE_BASES_COLLECTION,
                vectors=[query_embedding],
                filter={'knowledge_base_id': {'$in': accessible_ids}},
                limit=count,
            )

            if search_results and search_results.ids and search_results.ids[0]:
                result_ids = search_results.ids[0]
                result_distances = search_results.distances[0] if search_results.distances else [0] * len(result_ids)

                for knowledge_base_id, distance in zip(result_ids, result_distances):
                    if knowledge_base_id in seen_ids:
                        continue
                    seen_ids.add(knowledge_base_id)

                    if len(top_results_heap) < count:
                        heapq.heappush(top_results_heap, (distance, knowledge_base_id))
                    elif distance > top_results_heap[0][0]:
                        heapq.heapreplace(top_results_heap, (distance, knowledge_base_id))

            page_offset += page_size
            if len(accessible_knowledge_bases.items) < page_size:
                break
            if page_offset >= MAX_KNOWLEDGE_BASE_SEARCH_ITEMS:
                break

        # Sort by distance descending (best first) and fetch KB details
        sorted_results = sorted(top_results_heap, key=lambda x: x[0], reverse=True)

        matching_knowledge_bases = []
        for distance, knowledge_base_id in sorted_results:
            knowledge_base = await Knowledges.get_knowledge_by_id(knowledge_base_id)
            if knowledge_base:
                matching_knowledge_bases.append(
                    {
                        'id': knowledge_base.id,
                        'name': knowledge_base.name,
                        'description': knowledge_base.description or '',
                        'similarity': round(distance, 4),
                    }
                )

        return JSONCodec.dumps(matching_knowledge_bases, ensure_ascii=False)

    except Exception as e:
        log.exception(f'query_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Skills tools


async def view_skill(
    id: str,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read skill instructions and file list. Large skills return an opening preview and next_offset.

    :param id: Skill ID from the available skills manifest.
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        terminal_skill_prefix = 'terminal:'
        if isinstance(id, str) and id.startswith(terminal_skill_prefix):
            from open_webui.utils.terminals import get_terminal_skill

            skill_name = unquote(id.removeprefix(terminal_skill_prefix))
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                skill_name,
                {'__event_call__': __event_call__},
                overview=True,
            )
            if not skill:
                return JSONCodec.dumps({'error': f"Skill '{id}' not found"})
            return JSONCodec.dumps(skill, ensure_ascii=False)

        from types import SimpleNamespace

        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        actor = SimpleNamespace(**__user__)
        skill = await authorized_skill(id.lower(), actor)
        if not skill.is_active:
            await authorized_skill(skill.id, actor, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        return JSONCodec.dumps(
            {
                'id': skill.id,
                'name': snapshot['name'],
                **await skill_overview(snapshot['content'], file_summaries(snapshot['data']['files'])),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def read_skill_file(
    id: str,
    path: str,
    offset: int = 0,
    max_chars: int = 10000,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read a skill file from the loaded snapshot. Terminal skills support SKILL.md only.

    :param id: Skill ID.
    :param path: Relative path within the skill.
    :param offset: Character offset for paging text.
    :param max_chars: Maximum characters to return, capped by the admin skill content limit (default 10000).
    """
    try:
        from types import SimpleNamespace
        from urllib.parse import urlencode

        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        if not __user__ or __request__ is None:
            raise ValueError('Request and user context required')
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import get_terminal_skill

            if path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                offset=offset,
                max_chars=max_chars,
                refresh=False,
            )
            if not skill:
                raise ValueError(f"Skill '{id}' not found")
            return JSONCodec.dumps(
                {'path': path, 'content': skill['content'], 'next_offset': skill['next_offset']}, ensure_ascii=False
            )
        skill = await authorized_skill(id, SimpleNamespace(**__user__))
        if not skill.is_active:
            await authorized_skill(id, SimpleNamespace(**__user__), 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = versions.get(skill.id) or skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        file = next((f for f in snapshot['data']['files'] if f['path'] == path), None)
        if file is None:
            raise ValueError('File not found')
        if file.get('encoding'):
            return JSONCodec.dumps(
                {
                    **file_summaries([file])[0],
                    'url': '/workspace/skills/edit?' + urlencode({'id': id, 'path': path}),
                }
            )
        return JSONCodec.dumps(
            {
                'path': path,
                **await skill_content_page(file['content'], offset, max_chars),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def grep_skill_files(
    id: str,
    pattern: str,
    path: str | None = None,
    case_insensitive: bool = True,
    max_results: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Search skill files for text or regex matches.

    Literal text or auto-detected RE2 regex; no lookarounds/backreferences, and shorthand character classes are ASCII-only.
    Searches each line for non-empty, non-overlapping matches and returns merged, bounded excerpts (6000 characters total).
    Offsets and match_length describe the first match in each excerpt, in original-file characters; total_matches counts
    occurrences, not excerpts. Omitted excerpts have compact locations in omitted_matches; omitted_matches_truncated
    indicates when more locations remain. Terminal skills support SKILL.md only. No skill version is created.

    :param id: Skill ID, including terminal: IDs.
    :param pattern: Text or auto-detected regex to find (up to 4096 characters).
    :param path: Optional exact skill file path; otherwise search all text files, skipping binary files.
    :param case_insensitive: Ignore case when matching (default true).
    :param max_results: Maximum excerpts to return, from 1 to 30 (default 10).
    """
    from open_webui.utils.skill_files import SKILL_GREP_MAX_CHARS, grep_skill_content

    try:
        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        metadata = __metadata__ if __metadata__ is not None else {}
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import load_terminal_skill

            if path is not None and path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await load_terminal_skill(
                __request__,
                __user__,
                metadata,
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                refresh=False,
            )
            if skill is None:
                raise ValueError(f"Skill '{id}' not found")
            files = [{'path': 'SKILL.md', 'content': skill['content']}]
        else:
            from types import SimpleNamespace

            from open_webui.models.skills import get_skill_snapshot
            from open_webui.routers.skills import authorized_skill

            actor = SimpleNamespace(**__user__)
            skill = await authorized_skill(id, actor)
            if not skill.is_active:
                await authorized_skill(id, actor, 'write')
            context = metadata.get('chat_context') or {}
            metadata['chat_context'] = context
            versions = context.setdefault('skill_versions', {})
            version_id = versions.get(skill.id) or skill.version_id
            snapshot = await get_skill_snapshot(skill, version_id)
            versions[skill.id] = version_id
            files = snapshot['data']['files']
            if path is not None:
                file = next((file for file in files if file['path'] == path), None)
                if file is None:
                    raise ValueError('File not found')
                if file.get('encoding'):
                    raise ValueError('File is not a text file')
                files = [file]
        return await asyncio.to_thread(grep_skill_content, files, pattern, case_insensitive, max_results)
    except Exception as error:
        detail = str(getattr(error, 'detail', str(error)))
        result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        while len(result) > SKILL_GREP_MAX_CHARS:
            detail = detail[: max(0, len(detail) - (len(result) - SKILL_GREP_MAX_CHARS))]
            result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        return result


async def create_tool(
    id: str,
    name: str,
    description: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """Create a private Python tool in the workspace.
    Source must define a Tools class with public methods, type hints, and descriptive docstrings.
    Use Valves or UserValves for credentials; do not embed secrets. Saving executes the Python module on the server.

    :param id: Unique lowercase Python identifier for the toolkit.
    :param name: Display name.
    :param description: Short description of what the tool does.
    :param content: Complete Python source, without Markdown fences.
    """
    try:
        from types import SimpleNamespace

        from open_webui.models.tools import ToolForm
        from open_webui.routers.tools import create_new_tools
        from open_webui.utils.access_control import has_permission

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        if __user__.get('role') != 'admin' and not await has_permission(
            __user__['id'], 'workspace.tools', await Config.get('user.permissions')
        ):
            raise ValueError('Tool authoring permission required')
        result = await create_new_tools(
            __request__,
            ToolForm(id=id, name=name, content=content, meta={'description': description}, access_grants=[]),
            SimpleNamespace(**__user__),
            None,
        )
        return JSONCodec.dumps({'id': result.id, 'url': '/workspace/tools/edit?id=' + result.id})
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def create_skill(
    id: str,
    name: str,
    content: str,
    files: list[SkillFile] = None,
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """Create a private workspace skill with SKILL.md and optional UTF-8 supporting files. No terminal is needed.

    :param id: Unique lowercase skill slug.
    :param name: Display name.
    :param content: Complete SKILL.md text, including name and description frontmatter.
    :param files: Optional supporting files, each with path and content. UTF-8 text only.
    :param commit_message: Optional description of this save.
    """
    try:
        from types import SimpleNamespace

        from open_webui.models.skills import SkillForm
        from open_webui.routers.skills import create_new_skill
        from open_webui.utils.access_control import has_permission
        from open_webui.utils.skill_files import frontmatter

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        files = [f.model_dump(exclude_none=True) if isinstance(f, SkillFile) else f for f in files or []]
        if any(f.get('encoding') or f.get('path') == 'SKILL.md' for f in files):
            raise ValueError('Supporting files must be UTF-8 text; pass SKILL.md in content')
        # Agent authoring requires the workspace editor permission, not just import access.
        if __user__.get('role') != 'admin' and not await has_permission(
            __user__['id'], 'workspace.skills', await Config.get('user.permissions')
        ):
            raise ValueError('Skill authoring permission required')
        result = await create_new_skill(
            __request__,
            SkillForm(
                id=id,
                name=name,
                content=content,
                description=str(frontmatter(content).get('description', '')),
                files=[{'path': 'SKILL.md', 'content': content}, *(files or [])],
                commit_message=commit_message,
                access_grants=[],
            ),
            SimpleNamespace(**__user__),
            None,
        )
        return JSONCodec.dumps({'id': result.id, 'url': '/workspace/skills/edit?id=' + result.id})
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def update_skill_files(
    id: str,
    operations: list[SkillFileOperation],
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """Save file operations as a new skill version, preserving untouched files. If the skill changed since it was read, call view_skill again and reapply the edit.

    :param id: Skill to edit. Requires write access.
    :param operations: File operations: {op: put, path, content}, {op: move, path, destination}, or {op: delete, path}. Use put with path SKILL.md to edit the instructions. Text writes only.
    :param commit_message: Optional description of the change.
    """
    try:
        from types import SimpleNamespace

        from open_webui.events import EVENTS, publish_event
        from open_webui.models.skills import Skills
        from open_webui.routers.skills import authorized_skill

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        user = SimpleNamespace(**__user__)
        skill = await authorized_skill(id, user, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        expected_version_id = versions.get(skill.id) or skill.version_id
        operations = [
            op.model_dump(exclude_none=True) if isinstance(op, SkillFileOperation) else op for op in operations or []
        ]
        if any(op.get('encoding') for op in operations):
            raise ValueError('Agent file writes support UTF-8 text only')
        result = await Skills.update_skill_by_id(
            id,
            {
                'expected_version_id': expected_version_id,
                'operations': operations or [],
                'commit_message': commit_message,
            },
            user_id=user.id,
        )
        versions[skill.id] = result.version_id
        await publish_event(__request__, EVENTS.SKILL_UPDATED, actor=user, subject_id=id, data={'name': result.name})
        return JSONCodec.dumps({'id': result.id})
    except Exception as error:
        detail = getattr(error, 'detail', str(error))
        if isinstance(detail, dict) and detail.get('code') == 'version_conflict':
            detail = {
                'code': 'version_conflict',
                'message': 'Skill changed. Call view_skill again and reapply the edit.',
            }
        return JSONCodec.dumps({'error': detail})


# Tool search


async def search_tools(
    query: str,
    count: int = 5,
    __metadata__: dict = None,
) -> str:
    """
    Search the tools listed in <available_tools> and return their full definitions.
    Pass the exact tool name when you already know it.
    Invoke a result with call_tool(tool_name, params), where params is a JSON-encoded object
    matching the tool's parameters. Do not call deferred tools directly by name.

    :param query: Keywords describing the capability you need (e.g. "jira create issue"), or an exact tool name
    :param count: Maximum number of results to return (default: 5, max: 20)
    :return: JSON with the definitions of the matching tools, which can then be invoked through call_tool
    """
    from open_webui.utils.tool_search import search_deferred_tools

    tools = __metadata__['tools']
    candidates = {name: tools[name]['spec'] for name in __metadata__['deferred_tools']}
    matches = search_deferred_tools(query, candidates, count)
    if not matches:
        return JSONCodec.dumps(
            {'tools': [], 'message': 'No matching tools found. Try different keywords or the exact tool name.'}
        )
    return JSONCodec.dumps({'tools': [candidates[name] for name in matches]})


# Task management tools

from typing import Literal

from pydantic import BaseModel, Field

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a visible task checklist for multi-step work so progress can be shown in chat.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single visible task item as completed, in_progress, pending, or cancelled.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return JSONCodec.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return JSONCodec.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Registry-compatible exports for scheduled automations.
from open_webui.tools.automation_resources import (
    create_automation,
    delete_automation,
    list_automations,
    toggle_automation,
    update_automation,
)


# Registry-compatible exports for calendar tools.
from open_webui.tools.calendar_resources import (
    create_calendar_event,
    delete_calendar_event,
    search_calendar_events,
    update_calendar_event,
)
