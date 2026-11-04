"""Restricted Google Drive connector. Only the worker can invoke write routes."""

from __future__ import annotations

import base64
import json
import os
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


NATIVE_TO_OFFICE = {
    'application/vnd.google-apps.document': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
    'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
}
DRIVE_API = 'https://www.googleapis.com/drive/v3'
UPLOAD_API = 'https://www.googleapis.com/upload/drive/v3'
MAX_IMPORT_BYTES = 20 * 1024 * 1024


class DriveError(Exception):
    pass


def sheet_range(name: str, cells: str) -> str:
    return "'" + name.replace("'", "''") + "'!" + cells


class DriveClient:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(timeout=60, headers={'Authorization': f'Bearer {token}'})

    async def close(self):
        await self.client.aclose()

    async def request(self, method, url, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise DriveError(f'Google API {response.status_code}: {response.text[:500]}')
        return response

    async def request_content_limited(self, method, url, max_bytes, **kwargs):
        chunks = []
        size = 0
        async with self.client.stream(method, url, **kwargs) as response:
            if response.status_code >= 400:
                body = (await response.aread())[:500].decode(errors='replace')
                raise DriveError(f'Google API {response.status_code}: {body}')
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    limit = (f'{max_bytes // (1024 * 1024)} MB' if max_bytes % (1024 * 1024) == 0
                             else f'{max_bytes} byte')
                    raise DriveError(f'Google Drive file exceeds {limit} limit')
                chunks.append(chunk)
        return b''.join(chunks)

    async def metadata(self, file_id):
        response = await self.request('GET', f'{DRIVE_API}/files/{file_id}',
                                      params={'fields': 'id,name,mimeType,version,size,parents,capabilities(canEdit)',
                                              'supportsAllDrives': 'true'})
        return response.json()

    async def download(self, file_id, mime_type, max_bytes=MAX_IMPORT_BYTES):
        if mime_type in NATIVE_TO_OFFICE:
            office_mime, _ = NATIVE_TO_OFFICE[mime_type]
            url = f'{DRIVE_API}/files/{file_id}/export'
            params = {'mimeType': office_mime}
        else:
            url = f'{DRIVE_API}/files/{file_id}'
            params = {'alt': 'media', 'supportsAllDrives': 'true'}
        return await self.request_content_limited('GET', url, max_bytes, params=params)

    async def native_revision(self, file_id, mime_type):
        if mime_type == 'application/vnd.google-apps.document':
            url = f'https://docs.googleapis.com/v1/documents/{file_id}'
        elif mime_type == 'application/vnd.google-apps.presentation':
            url = f'https://slides.googleapis.com/v1/presentations/{file_id}'
        else:
            return None
        response = await self.request('GET', url, params={'fields': 'revisionId'})
        return response.json()['revisionId']

    async def find_created(self, document_id, role):
        query = (f"appProperties has {{ key='mh_document_id' and value='{document_id}' }} "
                 f"and appProperties has {{ key='mh_role' and value='{role}' }} and trashed = false")
        response = await self.request('GET', f'{DRIVE_API}/files',
                                      params={'q': query, 'fields': 'files(id,version),nextPageToken',
                                              'pageSize': 1, 'supportsAllDrives': 'true'})
        files = response.json().get('files', [])
        return files[0] if files else None

    async def upload(self, name, mime_type, content, file_id=None, parents=None, app_properties=None):
        if file_id:
            response = await self.request('PATCH', f'{UPLOAD_API}/files/{file_id}',
                                          params={'uploadType': 'media', 'supportsAllDrives': 'true'},
                                          headers={'Content-Type': mime_type}, content=content)
        else:
            boundary = 'mini_hermes_' + uuid4().hex
            metadata = {'name': name, 'mimeType': mime_type}
            if parents:
                metadata['parents'] = parents
            if app_properties:
                metadata['appProperties'] = app_properties
            body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
                    f'{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n').encode() + content + f'\r\n--{boundary}--'.encode()
            response = await self.request('POST', f'{UPLOAD_API}/files',
                                          params={'uploadType': 'multipart', 'fields': 'id,version'},
                                          headers={'Content-Type': f'multipart/related; boundary={boundary}'}, content=body)
        return response.json()

    async def apply_native(self, file_id, mime_type, operations, expected_revision=None):
        if mime_type == 'application/vnd.google-apps.document':
            if not expected_revision:
                raise DriveError('Google Docs write requires a revision precondition')
            requests = []
            for op in operations:
                if op['op'] == 'replace_text':
                    requests.append({'replaceAllText': {'containsText': {'text': op['find'], 'matchCase': True}, 'replaceText': op['replace']}})
                elif op['op'] == 'append_paragraph':
                    requests.append({'insertText': {'endOfSegmentLocation': {}, 'text': '\n' + op['text']}})
                else:
                    raise DriveError(f'Native Docs operation unsupported: {op["op"]}')
            await self.request('POST', f'https://docs.googleapis.com/v1/documents/{file_id}:batchUpdate',
                               json={'requests': requests, 'writeControl': {'requiredRevisionId': expected_revision}})
        elif mime_type == 'application/vnd.google-apps.presentation':
            if not expected_revision:
                raise DriveError('Google Slides write requires a revision precondition')
            requests = []
            for op in operations:
                if op['op'] == 'replace_text':
                    requests.append({'replaceAllText': {'containsText': {'text': op['find'], 'matchCase': True}, 'replaceText': op['replace']}})
                elif op['op'] == 'add_slide':
                    requests.append({'createSlide': {}})
                else:
                    raise DriveError(f'Native Slides operation unsupported: {op["op"]}')
            await self.request('POST', f'https://slides.googleapis.com/v1/presentations/{file_id}:batchUpdate',
                               json={'requests': requests, 'writeControl': {'requiredRevisionId': expected_revision}})
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            for op in operations:
                if op['op'] == 'set_cell':
                    cell_range = sheet_range(op['sheet'], op['cell'])
                    await self.request('PUT', f'https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values/{cell_range}',
                                       params={'valueInputOption': 'RAW'}, json={'values': [[op.get('value')]]})
                elif op['op'] == 'add_sheet':
                    await self.request('POST', f'https://sheets.googleapis.com/v4/spreadsheets/{file_id}:batchUpdate',
                                       json={'requests': [{'addSheet': {'properties': {'title': op['name']}}}]})
                elif op['op'] == 'append_row':
                    cell_range = sheet_range(op['sheet'], 'A:ZZ')
                    await self.request('POST', f'https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values/{cell_range}:append',
                                       params={'valueInputOption': 'RAW'}, json={'values': [op['values']]})
                else:
                    raise DriveError(f'Native Sheets operation unsupported: {op["op"]}')
        else:
            raise DriveError('Not a Google-native file')


class ConnectorRequest(BaseModel):
    access_token: str
    file_id: str | None = None
    mime_type: str | None = None
    name: str | None = None
    content_b64: str | None = None
    parents: list[str] | None = None
    operations: list[dict] | None = None
    document_id: str | None = None
    role: str | None = None
    app_properties: dict[str, str] | None = None
    expected_revision: str | None = None
    max_bytes: int | None = None


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _authorize(value: str | None):
    secret = os.getenv('MH_CONNECTOR_SECRET')
    if not secret or value != f'Bearer {secret}':
        raise HTTPException(403, 'Connector access denied')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/metadata')
async def metadata(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        return await drive.metadata(request.file_id)
    finally:
        await drive.close()


@app.post('/download')
async def download(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        max_bytes = min(request.max_bytes or MAX_IMPORT_BYTES, MAX_IMPORT_BYTES)
        content = await drive.download(request.file_id, request.mime_type, max_bytes=max_bytes)
        return {'content_b64': base64.b64encode(content).decode()}
    finally:
        await drive.close()


@app.post('/native-revision')
async def native_revision(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        return {'revision_id': await drive.native_revision(request.file_id, request.mime_type)}
    finally:
        await drive.close()


@app.post('/upload')
async def upload(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        return await drive.upload(request.name, request.mime_type, base64.b64decode(request.content_b64),
                                  request.file_id, request.parents, request.app_properties)
    finally:
        await drive.close()


@app.post('/find-created')
async def find_created(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        return {'file': await drive.find_created(request.document_id, request.role)}
    finally:
        await drive.close()


@app.post('/apply-native')
async def apply_native(request: ConnectorRequest, authorization: str | None = Header(None)):
    _authorize(authorization)
    drive = DriveClient(request.access_token)
    try:
        await drive.apply_native(request.file_id, request.mime_type, request.operations, request.expected_revision)
        return {'status': 'ok'}
    finally:
        await drive.close()


class Gateway:
    def __init__(self):
        self.base_url = os.getenv('MH_CONNECTOR_URL', 'http://127.0.0.1:8090')
        self.secret = os.getenv('MH_CONNECTOR_SECRET', '')

    async def call(self, route: str, **payload):
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f'{self.base_url}/{route}', json=payload,
                                         headers={'Authorization': f'Bearer {self.secret}'})
            if response.status_code >= 400:
                raise DriveError(f'Connector {response.status_code}: {response.text[:500]}')
            return response.json()
