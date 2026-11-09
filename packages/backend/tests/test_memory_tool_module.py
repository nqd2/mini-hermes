import pytest

from open_webui.tools import builtin, memory_resources
from open_webui.utils.json_codec import JSONCodec


def _tool_user():
    return {
        'id': 'alice',
        'email': 'alice@example.test',
        'name': 'Alice',
        'last_active_at': 1,
        'updated_at': 1,
        'created_at': 1,
    }


def test_memory_tools_keep_legacy_builtin_imports():
    for name in (
        'list_memory_paths',
        'read_memory_path',
        'search_memories',
        'add_memory',
        'update_memory',
        'replace_memory_content',
        'delete_memory',
        'list_memories',
    ):
        assert getattr(builtin, name) is getattr(memory_resources, name)


@pytest.mark.asyncio
async def test_list_memory_paths_keeps_form_normalization_and_user_scope(monkeypatch):
    captured = {}

    async def list_paths(form_data, user):
        captured['form'] = form_data
        captured['user'] = user
        return {'paths': [{'path': 'work', 'count': 2}]}

    monkeypatch.setattr(memory_resources, '_list_memory_paths', list_paths)

    result = JSONCodec.loads(
        await builtin.list_memory_paths(
            query='',
            count=8,
            type='unknown',
            __user__=_tool_user(),
        )
    )

    assert result == {'paths': [{'path': 'work', 'count': 2}]}
    assert captured['form'].query is None
    assert captured['form'].type == 'all'
    assert captured['form'].limit == 8
    assert captured['user'].id == 'alice'


@pytest.mark.asyncio
async def test_memory_writes_require_request_context_before_calling_service(monkeypatch):
    async def unexpected_add(*_args, **_kwargs):
        raise AssertionError('memory write ran without a request')

    monkeypatch.setattr(memory_resources, '_add_memory', unexpected_add)

    result = JSONCodec.loads(await builtin.add_memory('remember this', __request__=None, __user__=_tool_user()))

    assert result == {'error': 'Request context not available'}
