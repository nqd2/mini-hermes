from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.tools import builtin
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils import tools


@pytest.mark.asyncio
async def test_retired_builtin_categories_stay_unavailable_with_stale_flags(monkeypatch):
    """Old saved model flags cannot re-enable retired agent capabilities."""
    monkeypatch.setattr(tools.Config, "get_many", AsyncMock(return_value={
        "notes.enable": True,
        "channels.enable": True,
        "automations.enable": True,
        "calendar.enable": True,
        "subagents.enable": True,
        "ui.enable_user_webhooks": True,
    }))
    request = SimpleNamespace(state=SimpleNamespace(internal=False, direct=False))
    model = {"id": "test-model", "info": {"meta": {"builtinTools": {
        "time": False,
        "user_input": False,
        "files": False,
        "knowledge": False,
        "chats": False,
        "memory": False,
        "web_search": False,
        "image_generation": False,
        "code_interpreter": False,
        "skills": False,
        "tools": False,
        "tasks": False,
        "mini_hermes_documents": False,
        "notes": True,
        "channels": True,
        "automations": True,
        "calendar": True,
        "subagents": True,
        "notifications": True,
    }}}}

    available = await tools.get_builtin_tools(
        request, {"__user__": {"id": "owner", "role": "admin"}}, model=model,
    )

    assert available == {}


@pytest.mark.asyncio
async def test_agent_image_generation_requires_a_resource_aware_office_document(monkeypatch):
    """The agent may only offer image creation while handling an Office document."""
    monkeypatch.setattr(tools.Config, 'get_many', AsyncMock(return_value={
        'image_generation.enable': True,
        'images.edit.enable': True,
    }))
    request = SimpleNamespace(state=SimpleNamespace(internal=False, direct=False))
    model = {'id': 'test-model', 'info': {'meta': {'builtinTools': {'image_generation': True}}}}
    params = {
        '__user__': {'id': 'owner', 'role': 'admin'},
        '__metadata__': {
            'resource_aware_files': True,
            'resource_model_tools': True,
            'resource_chat_has_files': True,
        },
    }

    without_document = await tools.get_builtin_tools(request, params, features={'image_generation': True}, model=model)
    assert 'generate_image' not in without_document
    assert 'edit_image' not in without_document

    with_pdf = await tools.get_builtin_tools(
        request,
        {
            **params,
            '__metadata__': {
                **params['__metadata__'],
                'resource_files': [{'id': 'file-1', 'name': 'report.pdf'}],
            },
        },
        features={},
        model=model,
    )
    assert 'generate_image' not in with_pdf

    with_office_document = await tools.get_builtin_tools(
        request,
        {
            **params,
            '__metadata__': {
                **params['__metadata__'],
                'resource_files': [{'id': 'file-1', 'name': 'report.docx'}],
                'resource_scope_ids': ['file-1'],
            },
        },
        features={},
        model=model,
    )
    assert 'generate_image' in with_office_document
    assert 'edit_image' not in with_office_document


@pytest.mark.asyncio
async def test_generate_image_requires_saved_ask_user_acceptance(monkeypatch):
    """A provider call cannot bypass the durable document-image choice."""
    image_generations = AsyncMock(return_value=[{'id': 'image-1', 'url': '/files/image-1.png'}])
    monkeypatch.setattr(builtin, 'image_generations', image_generations)
    monkeypatch.setattr(builtin, 'upload_files_to_terminal', AsyncMock())
    monkeypatch.setattr(builtin.Chats, 'add_message_files_by_id_and_message_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(builtin.Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id='owner')))
    monkeypatch.setattr(builtin.Chats, 'get_message_by_id_and_message_id', AsyncMock(return_value={'output': []}))

    result = JSONCodec.loads(
        await builtin.generate_image(
            'A chart for the report',
            __request__=SimpleNamespace(),
            __user__={
                'id': 'owner',
                'email': 'owner@example.test',
                'name': 'Owner',
                'role': 'user',
                'last_active_at': 1,
                'updated_at': 1,
                'created_at': 1,
            },
            __chat_id__='chat-1',
            __message_id__='assistant-1',
            __metadata__={
                'resource_aware_files': True,
                'resource_files': [{'id': 'file-1', 'name': 'report.xlsx'}],
                'resource_scope_ids': ['file-1'],
            },
        )
    )

    assert result['error'] == 'Document image generation requires a saved user choice.'
    image_generations.assert_not_awaited()
