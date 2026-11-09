from types import SimpleNamespace

import pytest

from open_webui.tools import builtin, note_resources
from open_webui.utils.json_codec import JSONCodec

NOTE_TOOL_NAMES = ('search_notes', 'view_note', 'write_note', 'replace_note_content')


def test_note_tools_keep_legacy_builtin_imports():
    for name in NOTE_TOOL_NAMES:
        assert getattr(builtin, name) is getattr(note_resources, name)


@pytest.mark.asyncio
async def test_note_tools_require_request_context_before_accessing_models(monkeypatch):
    async def unexpected_call(*_args, **_kwargs):
        raise AssertionError('note model was accessed without a request')

    for method_name in ('search_notes', 'get_note_by_id', 'insert_new_note', 'update_note_by_id'):
        monkeypatch.setattr(note_resources.Notes, method_name, unexpected_call)

    calls = (
        builtin.search_notes('query', __request__=None, __user__={'id': 'alice'}),
        builtin.view_note('note-1', __request__=None, __user__={'id': 'alice'}),
        builtin.write_note('Title', 'Body', __request__=None, __user__={'id': 'alice'}),
        builtin.replace_note_content('note-1', content='Body', __request__=None, __user__={'id': 'alice'}),
    )

    for call in calls:
        assert JSONCodec.loads(await call) == {'error': 'Request context not available'}


@pytest.mark.asyncio
async def test_view_note_returns_content_and_records_page_revision(monkeypatch):
    page = {
        'id': 'page-1',
        'title': 'Overview',
        'parent_id': None,
        'revision': 4,
        'content': {'md': 'The note body.'},
    }
    note = SimpleNamespace(
        id='note-1',
        title='Project notes',
        user_id='alice',
        data={'pages': [page], 'content': {'md': 'The note body.'}},
        updated_at=20,
        created_at=10,
    )

    async def get_note_by_id(_note_id):
        return note

    async def get_groups_by_member_id(_user_id, include_inherited=True):
        return []

    monkeypatch.setattr(note_resources.Notes, 'get_note_by_id', get_note_by_id)
    monkeypatch.setattr(note_resources.Groups, 'get_groups_by_member_id', get_groups_by_member_id)
    monkeypatch.setattr(note_resources, 'get_page', lambda _data, _page_id: page)
    request = SimpleNamespace(state=SimpleNamespace())

    result = JSONCodec.loads(
        await builtin.view_note('note-1', __request__=request, __user__={'id': 'alice', 'role': 'user'})
    )

    assert result['content'] == 'The note body.'
    assert result['pages'] == [{'id': 'page-1', 'title': 'Overview', 'parent_id': None}]
    assert request.state.note_page_revisions[('alice', 'note-1', 'page-1')] == 4
