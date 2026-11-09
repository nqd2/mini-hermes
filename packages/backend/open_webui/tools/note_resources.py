"""Built-in tools for reading, searching, and updating saved notes."""

# Keep Optional annotations unchanged so legacy tool signatures remain stable.
# ruff: noqa: UP045

import logging
from typing import Optional

from fastapi import Request

from open_webui.events import EVENTS, publish_event
from open_webui.models.groups import Groups
from open_webui.models.notes import NotePageConflict, Notes, get_content_files, get_page, matching_page
from open_webui.models.users import Users
from open_webui.socket.main import close_changed_note_documents, sio
from open_webui.utils.access_control.files import has_access_to_note_files
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)


async def _has_write_access_to_note(note, user_id: str) -> bool:
    if note.user_id == user_id:
        return True

    from open_webui.models.access_grants import AccessGrants

    user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
    return await AccessGrants.has_access(
        user_id=user_id,
        resource_type='note',
        resource_id=note.id,
        permission='write',
        user_group_ids=set(user_group_ids),
    )


async def _emit_note_updated(request: Request, user: dict, note) -> None:
    await sio.emit('events:note', note.model_dump(), to=f'note:{note.id}')
    await publish_event(
        request,
        EVENTS.NOTE_UPDATED,
        actor=user,
        subject_id=note.id,
        data={'title': note.title},
    )


async def search_notes(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's saved notes by title and content.

    :param query: The search query to find matching notes
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include notes updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include notes updated before this Unix timestamp (seconds)
    :return: JSON with matching notes containing id, title, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Notes.search_notes(
            user_id=user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
                'permission': 'read',
            },
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        # Convert timestamps to nanoseconds for comparison
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        notes = []
        for note in result.items:
            # Apply date filters (updated_at is in nanoseconds)
            if start_ts and note.updated_at < start_ts:
                continue
            if end_ts and note.updated_at > end_ts:
                continue

            # Extract a snippet from the matching page.
            page = matching_page(note.data, query)
            content_snippet = ''
            if page['content'].get('md'):
                md_content = page['content']['md']
                content_lower = md_content.lower()

                # Find the first matching word to center the snippet around.
                search_words = query.lower().split()
                match_pos = -1
                match_len = len(query)
                for word in search_words:
                    found_pos = content_lower.find(word)
                    if found_pos != -1:
                        match_pos = found_pos
                        match_len = len(word)
                        break

                if match_pos != -1:
                    snippet_start = max(0, match_pos - 50)
                    snippet_end = min(len(md_content), match_pos + match_len + 100)
                    content_snippet = (
                        ('...' if snippet_start > 0 else '')
                        + md_content[snippet_start:snippet_end]
                        + ('...' if snippet_end < len(md_content) else '')
                    )
                else:
                    content_snippet = md_content[:150] + ('...' if len(md_content) > 150 else '')

            notes.append(
                {
                    'id': note.id,
                    'title': note.title,
                    'snippet': content_snippet,
                    'page_id': page['id'],
                    'page_title': page['title'],
                    'url': f'/notes/{note.id}?page={page["id"]}',
                    'updated_at': note.updated_at,
                }
            )

            if len(notes) >= count:
                break

        return JSONCodec.dumps(notes, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_notes error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_note(
    note_id: str,
    page_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a note by its ID.

    :param note_id: The ID of the note to retrieve
    :param page_id: Page to read; omit to inspect the complete note and its page directory
    :return: JSON with the note's id, title, and full markdown content
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found'})

        # Check access permission
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        from open_webui.models.access_grants import AccessGrants

        if (
            __user__.get('role') != 'admin'
            and note.user_id != user_id
            and not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='note',
                resource_id=note.id,
                permission='read',
                user_group_ids=set(user_group_ids),
            )
        ):
            return JSONCodec.dumps({'error': 'Access denied'})

        page = get_page(note.data, page_id) if page_id or len(note.data['pages']) == 1 else None
        content = page['content'].get('md', '') if page else note.data['content'].get('md', '')
        # Keep the version read by this tool run out of the model-facing interface.
        if not hasattr(__request__.state, 'note_page_revisions'):
            __request__.state.note_page_revisions = {}
        for read_page in [page] if page else note.data['pages']:
            __request__.state.note_page_revisions[(user_id, note.id, read_page['id'])] = read_page.get('revision', 0)

        return JSONCodec.dumps(
            {
                'id': note.id,
                'title': note.title,
                'content': content,
                'page_id': page['id'] if page else None,
                'page_title': page['title'] if page else None,
                'pages': [
                    {'id': p['id'], 'title': p['title'], 'parent_id': p.get('parent_id')} for p in note.data['pages']
                ],
                'updated_at': note.updated_at,
                'created_at': note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def write_note(
    title: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a new note with the given title and content.

    :param title: The title of the new note
    :param content: The markdown content for the note
    :return: JSON with success status and new note id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteForm

        user_id = __user__.get('id')

        form = NoteForm(
            title=title,
            data={'content': {'md': content}},
            access_grants=[],  # Private by default - only owner can access
        )

        editor = await Users.get_user_by_id(user_id)
        if not editor or not await has_access_to_note_files(None, get_content_files(form.data['content']), editor):
            return JSONCodec.dumps({'error': 'Cannot attach files you cannot access'})
        new_note = await Notes.insert_new_note(user_id, form)

        if not new_note:
            return JSONCodec.dumps({'error': 'Failed to create note'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': new_note.id,
                'title': new_note.title,
                'created_at': new_note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'write_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def replace_note_content(
    note_id: str,
    content: Optional[str] = None,
    operations: Optional[list[dict]] = None,
    title: Optional[str] = None,
    page_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing note by replacing the whole markdown content, exact text, or character ranges.

    Read the note with view_note before editing. If it changes meanwhile, read it again before retrying.
    Prefer "replace_text" when only part of the note changes. Supply either find or start/end, not both.
    find is non-empty, literal, and case-sensitive.
    Text matches are non-overlapping. Multiple matches require a 0-indexed occurrence or replace_all=true;
    otherwise nothing is changed and match offsets and context are returned.
    A "replace" operation must be the only operation in the request.
    start and end are 0-indexed character offsets into the markdown content from view_note.
    end is exclusive.
    Offsets never shift as operations are applied.
    Ranges must not overlap.
    expected is optional. When set, the request is rejected if the range's current text does not match it.
    "replace_range" is a deprecated alias for the start/end form of "replace_text".

    :param note_id: The ID of the note to update
    :param content: The new markdown content for a whole-note update
    :param operations: Optional note operations:
    - {"action": "replace", "content": "..."}
    - {"action": "replace_text", "start": 0, "end": 10, "content": "...", "expected": "..."}
    - {"action": "replace_text", "find": "old text", "content": "new text", "occurrence": 0, "replace_all": false}
    :param title: Optional new title for the note
    :param page_id: Page to edit; required for notes with multiple pages. Use the active page_id supplied in the request unless the user selects another page
    :return: JSON with success status and updated note info
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteUpdateForm

        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found', 'code': 'not_found'})

        user_id = __user__.get('id')
        if __user__.get('role') != 'admin' and not await _has_write_access_to_note(note, user_id):
            return JSONCodec.dumps({'error': 'Write access denied', 'code': 'write_access_denied'})

        page = get_page(note.data, page_id)
        page_id = page['id']
        read_revisions = getattr(__request__.state, 'note_page_revisions', {})
        read_key = (user_id, note.id, page_id)
        if read_key not in read_revisions:
            return JSONCodec.dumps({'error': 'Read this page with view_note before editing', 'code': 'read_required'})
        read_revision = read_revisions[read_key]
        if read_revision != page.get('revision', 0):
            raise NotePageConflict('Page changed since you read it. Use view_note again before editing')
        current_content = page['content'].get('md') or ''
        applied_operation_count = 0
        range_operations = []
        if operations is not None:
            if not isinstance(operations, list) or len(operations) == 0:
                return JSONCodec.dumps({'error': 'operations must be a non-empty list', 'code': 'invalid_operations'})

            for idx, operation in enumerate(operations):
                if not isinstance(operation, dict):
                    return JSONCodec.dumps(
                        {'error': 'each operation must be an object', 'code': 'invalid_operation', 'index': idx}
                    )

                action = operation.get('action')
                replacement = operation.get('content')

                if action == 'replace':
                    if len(operations) != 1:
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation must be the only operation',
                                'code': 'invalid_operations',
                                'index': idx,
                            }
                        )
                    if not isinstance(replacement, str):
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation content must be a string',
                                'code': 'invalid_content',
                                'index': idx,
                            }
                        )
                    content = replacement
                    applied_operation_count = 1
                    break

                if action not in ('replace_range', 'replace_text'):
                    return JSONCodec.dumps(
                        {'error': 'unknown operation action', 'code': 'invalid_action', 'index': idx, 'action': action}
                    )

                if not isinstance(replacement, str):
                    return JSONCodec.dumps(
                        {'error': 'operation content must be a string', 'code': 'invalid_content', 'index': idx}
                    )
                if action == 'replace_text' and 'find' in operation:
                    if 'start' in operation or 'end' in operation:
                        return JSONCodec.dumps(
                            {
                                'error': 'use either find or start/end, not both',
                                'code': 'invalid_operation',
                                'index': idx,
                            }
                        )
                    find = operation.get('find')
                    occurrence = operation.get('occurrence')
                    replace_all = operation.get('replace_all', False)
                    if not isinstance(find, str) or not find:
                        return JSONCodec.dumps(
                            {'error': 'find must be a non-empty string', 'code': 'invalid_find', 'index': idx}
                        )
                    if (
                        type(replace_all) is not bool
                        or (occurrence is not None and (type(occurrence) is not int or occurrence < 0))
                        or (replace_all and occurrence is not None)
                    ):
                        return JSONCodec.dumps(
                            {
                                'error': 'use a non-negative occurrence or replace_all boolean',
                                'code': 'invalid_occurrence',
                                'index': idx,
                            }
                        )

                    matches = []
                    start = current_content.find(find)
                    while start != -1:
                        end = start + len(find)
                        matches.append({'start': start, 'end': end})
                        start = current_content.find(find, end)

                    if not matches:
                        return JSONCodec.dumps({'error': 'Text not found', 'code': 'text_not_found', 'index': idx})
                    if occurrence is not None and occurrence >= len(matches):
                        return JSONCodec.dumps(
                            {'error': 'occurrence is out of bounds', 'code': 'invalid_occurrence', 'index': idx}
                        )
                    if len(matches) > 1 and occurrence is None and not replace_all:
                        return JSONCodec.dumps(
                            {
                                'error': 'Multiple matches; specify occurrence or replace_all',
                                'code': 'ambiguous_match',
                                'index': idx,
                                'matches': [
                                    {
                                        **match,
                                        'context': current_content[max(0, match['start'] - 50) : match['end'] + 50],
                                    }
                                    for match in matches
                                ],
                            },
                            ensure_ascii=False,
                        )
                    if occurrence is not None:
                        matches = [matches[occurrence]]
                    range_operations.extend({**match, 'content': replacement} for match in matches)
                    continue

                start = operation.get('start')
                end = operation.get('end')
                expected = operation.get('expected')
                if not isinstance(start, int) or not isinstance(end, int):
                    return JSONCodec.dumps(
                        {'error': 'operation start and end must be integers', 'code': 'invalid_range', 'index': idx}
                    )
                if start < 0 or end < start or end > len(current_content):
                    return JSONCodec.dumps(
                        {'error': 'operation range is out of bounds', 'code': 'range_out_of_bounds', 'index': idx}
                    )
                if expected is not None and current_content[start:end] != expected:
                    return JSONCodec.dumps(
                        {
                            'error': 'operation expected text does not match current content',
                            'code': 'expected_mismatch',
                            'index': idx,
                        }
                    )

                range_operations.append({'start': start, 'end': end, 'content': replacement})

            range_operations.sort(key=lambda operation: operation['start'])
            previous_end = 0
            for idx, operation in enumerate(range_operations):
                if operation['start'] < previous_end:
                    return JSONCodec.dumps(
                        {'error': 'operation ranges must not overlap', 'code': 'overlapping_operations', 'index': idx}
                    )
                previous_end = operation['end']

            if range_operations:
                content = current_content
                for operation in reversed(range_operations):
                    content = content[: operation['start']] + operation['content'] + content[operation['end'] :]
                applied_operation_count = len(range_operations)
        elif content is None:
            return JSONCodec.dumps({'error': 'content or operations is required', 'code': 'content_required'})

        update_data = {
            'page_id': page_id,
            'page_revision': read_revision,
            'data': {
                'content': {
                    'json': None,
                    'html': '',
                    'md': content,
                },
            },
        }
        if title:
            update_data['title'] = title

        editor = await Users.get_user_by_id(__user__['id'])
        if not editor or not await has_access_to_note_files(
            note, get_content_files(update_data['data']['content']), editor
        ):
            return JSONCodec.dumps({'error': 'Cannot attach files you cannot access'})
        form = NoteUpdateForm(**update_data)
        updated_note = await Notes.update_note_by_id(note_id, form)

        if not updated_note:
            return JSONCodec.dumps({'error': 'Failed to update note', 'code': 'update_failed'})

        read_revisions[read_key] = get_page(updated_note.data, page_id).get('revision', 0)
        await close_changed_note_documents(note, updated_note)
        await _emit_note_updated(__request__, __user__, updated_note)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated_note.id,
                'page_id': page_id,
                'title': updated_note.title,
                'updated_at': updated_note.updated_at,
                'applied_operation_count': applied_operation_count,
                'applied_ranges': [{'start': op['start'], 'end': op['end']} for op in range_operations],
            },
            ensure_ascii=False,
        )
    except NotePageConflict as e:
        return JSONCodec.dumps({'error': str(e), 'code': 'page_conflict'})
    except Exception as e:
        log.exception(f'replace_note_content error: {e}')
        return JSONCodec.dumps({'error': str(e), 'code': 'unexpected_error'})
