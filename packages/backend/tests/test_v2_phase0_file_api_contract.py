"""Characterize selected existing file upload/status wire behavior, not V2 ingestion."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from open_webui.routers import files


@pytest.mark.asyncio
async def test_upload_route_keeps_multipart_contract_and_publishes_file_id(monkeypatch):
    seen = {}

    async def handler(_request, **kwargs):
        seen['file'] = kwargs['file'].filename
        seen['metadata'] = kwargs['metadata']
        seen['process'] = kwargs['process']
        return {
            'id': 'file-1', 'user_id': 'owner', 'filename': 'book.xlsx',
            'created_at': 1700000000, 'data': {}, 'meta': {'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        }

    async def publish(_request, event, **kwargs):
        seen['event'] = event
        seen['subject_id'] = kwargs['subject_id']

    monkeypatch.setattr(files, 'upload_file_handler', handler)
    monkeypatch.setattr(files, 'publish_event', publish)
    app = FastAPI()
    app.include_router(files.router, prefix='/api/v1/files')
    app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    app.dependency_overrides[files.get_async_session] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/v1/files/?process=false',
            files={'file': ('book.xlsx', b'workbook bytes', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
            data={'metadata': '{"mini_hermes_chat_upload":true}'},
        )
    assert response.status_code == 200
    assert response.json()['id'] == 'file-1'
    assert seen['file'] == 'book.xlsx'
    assert seen['metadata'] == '{"mini_hermes_chat_upload":true}'
    assert seen['process'] is False
    assert seen['subject_id'] == 'file-1'


@pytest.mark.asyncio
async def test_process_status_stream_frames_and_current_open_check(monkeypatch):
    async def get_file(_id):
        return SimpleNamespace(
            id='file-1', user_id='owner', data={'status': 'completed'},
            model_dump=lambda: {'data': {'status': 'completed'}},
        )

    async def denied(*_args, **_kwargs):
        return False

    monkeypatch.setattr(files.Files, 'get_file_by_id', get_file)
    monkeypatch.setattr(files, 'has_access_to_file', denied)
    app = FastAPI()
    app.include_router(files.router, prefix='/api/v1/files')
    app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        ordinary = await client.get('/api/v1/files/file-1/process/status')
        streamed = await client.get('/api/v1/files/file-1/process/status?stream=true')
        app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='other', role='user')
        forbidden = await client.get('/api/v1/files/file-1/process/status?stream=true')
    assert ordinary.status_code == 200 and ordinary.json() == {'status': 'completed'}
    assert streamed.status_code == 200
    assert streamed.headers['content-type'].startswith('text/event-stream')
    assert streamed.text == 'data: {"status":"completed"}\n\n'
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_original_content_is_downloaded_only_after_current_access_check(monkeypatch, tmp_path):
    source = tmp_path / 'book.xlsx'
    source.write_bytes(b'original workbook bytes')

    async def get_file(_id, db=None):
        return SimpleNamespace(
            id='file-1', user_id='owner', filename='book.xlsx', path='object-key',
            meta={'name': 'book.xlsx', 'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        )

    async def denied(*_args, **_kwargs):
        return False

    monkeypatch.setattr(files.Files, 'get_file_by_id', get_file)
    monkeypatch.setattr(files, 'has_access_to_file', denied)
    monkeypatch.setattr(files.Storage, 'get_file', lambda _path: str(source))
    app = FastAPI()
    app.include_router(files.router, prefix='/api/v1/files')
    app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    app.dependency_overrides[files.get_async_session] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        allowed = await client.get('/api/v1/files/file-1/content')
        app.dependency_overrides[files.get_verified_user] = lambda: SimpleNamespace(id='other', role='user')
        forbidden = await client.get('/api/v1/files/file-1/content')
    assert allowed.status_code == 200 and allowed.content == b'original workbook bytes'
    assert allowed.headers['content-disposition'].startswith('attachment;')
    assert forbidden.status_code == 404
