"""Characterize public document route contracts without touching stored documents."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from open_webui.mini_hermes import revision_service, routes


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1/mini-hermes')
    app.dependency_overrides[routes.get_verified_user] = lambda: SimpleNamespace(id='user-1', role='user')
    app.dependency_overrides[routes.get_async_session] = lambda: None
    monkeypatch.setattr(routes, 'ObjectStore', lambda: object())
    return app


@pytest.mark.asyncio
async def test_commit_preserves_approval_fields_and_delegates_to_revision_service(app, monkeypatch):
    publish = AsyncMock(return_value=(SimpleNamespace(id='revision-2', number=2), None))
    dispatch = AsyncMock()
    monkeypatch.setattr(routes, 'publish_revision', publish)
    monkeypatch.setattr(revision_service, 'dispatch_pending_outbox', dispatch)
    approval = {
        'base_revision': 1,
        'base_sha256': 'a' * 64,
        'artifact_sha256': 'b' * 64,
        'patch_hash': 'c' * 64,
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/v1/mini-hermes/previews/preview-1/commit',
            json=approval,
            headers={'Idempotency-Key': 'commit-1'},
        )

    assert response.status_code == 200
    assert response.json() == {'revision': 2, 'sync_job_id': None, 'sync_status': 'local_only'}
    publish.assert_awaited_once()
    assert publish.await_args.args[2:] == ('user-1', 'preview-1', False)
    assert publish.await_args.kwargs == {'approval': approval, 'idempotency_key': 'commit-1'}
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args[2] == 'revision-2'


@pytest.mark.asyncio
async def test_commit_rejects_malformed_approval_before_revision_service(app, monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(routes, 'publish_revision', publish)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/v1/mini-hermes/previews/preview-1/commit',
            json={'base_revision': 1, 'base_sha256': 'invalid', 'artifact_sha256': 'b' * 64, 'patch_hash': 'c' * 64},
        )

    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_denies_write_without_creating_a_preview(app, monkeypatch):
    access = AsyncMock(side_effect=HTTPException(status_code=404, detail='Document not found'))
    create = AsyncMock()
    monkeypatch.setattr(routes, 'accessible_document', access)
    monkeypatch.setattr(routes, 'create_preview', create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/v1/mini-hermes/documents/doc-1/previews',
            json={'operations': [{'op': 'set_cell', 'sheet': 'Sheet', 'cell': 'A1', 'value': 'x'}]},
        )

    assert response.status_code == 404
    assert response.json() == {'detail': 'Document not found'}
    access.assert_awaited_once_with(None, 'doc-1', 'user-1', 'write', is_admin=False)
    create.assert_not_awaited()
