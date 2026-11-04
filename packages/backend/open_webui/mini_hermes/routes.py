"""Authenticated document API for Open WebUI."""

from __future__ import annotations

import base64
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select

from open_webui.internal.db import get_async_session
from open_webui.utils.auth import get_verified_user

from .drive import Gateway, NATIVE_TO_OFFICE
from .models import Document, Preview, Revision, SyncJob
from .oauth import access_token_for, authorization_url, exchange_code, save_refresh_token
from .service import MAX_BYTES, commit_preview, create_document, create_preview, owned_document, revision_for
from .storage import ObjectStore


router = APIRouter()


class LinkRequest(BaseModel):
    url: str


class PreviewRequest(BaseModel):
    operations: list[dict]


def _file_id(url: str) -> str:
    pattern = r'https://(?:docs|drive)\.google\.com/(?:document/d/|spreadsheets/d/|presentation/d/|file/d/|open\?id=)([A-Za-z0-9_-]+)'
    match = re.match(pattern, url)
    if not match:
        raise HTTPException(400, 'Use a Google Docs, Sheets, Slides or Drive file link')
    return match.group(1)


def _document_json(doc: Document, job: SyncJob | None = None):
    public_status = 'pending' if job and job.status == 'processing' else job.status if job else None
    return {'id': doc.id, 'filename': doc.filename, 'mime_type': doc.mime_type,
            'revision': doc.current_revision, 'drive_file_id': doc.drive_file_id,
            'drive_mime_type': doc.drive_mime_type,
            'office_companion_id': doc.office_companion_id,
            'sync': None if job is None else {'status': public_status, 'attempts': job.attempts, 'error': job.error}}


async def _visible_sync_job(db, document_id: str) -> SyncJob | None:
    job = (await db.execute(select(SyncJob).where(
        SyncJob.document_id == document_id, SyncJob.status != 'synced')
        .order_by(SyncJob.created_at).limit(1))).scalar_one_or_none()
    if job:
        return job
    return (await db.execute(select(SyncJob).where(SyncJob.document_id == document_id)
                             .order_by(SyncJob.created_at.desc()).limit(1))).scalar_one_or_none()


@router.get('/documents')
async def list_documents(user=Depends(get_verified_user), db=Depends(get_async_session)):
    docs = (await db.execute(select(Document).where(Document.owner_id == user.id).order_by(Document.created_at.desc()))).scalars().all()
    result = []
    for doc in docs:
        job = await _visible_sync_job(db, doc.id)
        result.append(_document_json(doc, job))
    return result


@router.post('/documents/upload')
async def upload_document(file: UploadFile = File(...), user=Depends(get_verified_user), db=Depends(get_async_session)):
    content = await file.read(MAX_BYTES + 1)
    doc = await create_document(db, ObjectStore(), user.id, Path(file.filename or '').name, content)
    return _document_json(doc)


@router.post('/documents/import-drive')
async def import_drive(request: LinkRequest, user=Depends(get_verified_user), db=Depends(get_async_session)):
    token = await access_token_for(db, user.id)
    gateway = Gateway()
    file_id = _file_id(request.url)
    metadata = await gateway.call('metadata', access_token=token, file_id=file_id)
    if not metadata.get('capabilities', {}).get('canEdit'):
        raise HTTPException(403, 'Drive file is not editable by this user')
    if int(metadata.get('size') or 0) > MAX_BYTES:
        raise HTTPException(413, 'Drive file exceeds 20 MB limit')
    mime_type = metadata['mimeType']
    if mime_type in NATIVE_TO_OFFICE:
        _, extension = NATIVE_TO_OFFICE[mime_type]
        filename = metadata['name'] + extension
    else:
        filename = metadata['name']
    content = base64.b64decode((await gateway.call('download', access_token=token, file_id=file_id,
                                                  mime_type=mime_type, max_bytes=MAX_BYTES))['content_b64'])
    doc = await create_document(db, ObjectStore(), user.id, filename, content,
                                drive_file_id=file_id, drive_mime_type=mime_type,
                                drive_version=str(metadata['version']))
    return _document_json(doc)


@router.get('/documents/{document_id}')
async def get_document(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await owned_document(db, document_id, user.id)
    job = await _visible_sync_job(db, doc.id)
    return _document_json(doc, job)


@router.get('/documents/{document_id}/content')
async def get_content(document_id: str, revision: int | None = None,
                      user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await owned_document(db, document_id, user.id)
    record = await revision_for(db, doc.id, revision or doc.current_revision)
    return Response(await ObjectStore().get(record.object_key), media_type=doc.mime_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(doc.filename)}"})


@router.post('/documents/{document_id}/previews')
async def request_preview(document_id: str, request: PreviewRequest,
                          user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await owned_document(db, document_id, user.id)
    preview = await create_preview(db, doc, user.id, request.operations)
    return {'id': preview.id, 'status': preview.status}


@router.get('/documents/{document_id}/previews')
async def list_previews(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    await owned_document(db, document_id, user.id)
    previews = (await db.execute(select(Preview).where(Preview.document_id == document_id,
                                                       Preview.owner_id == user.id)
                                 .order_by(Preview.created_at.desc()).limit(10))).scalars().all()
    return [{'id': item.id, 'status': item.status, 'diff': item.diff, 'error': item.error,
             'operations': item.operations, 'base_revision': item.base_revision} for item in previews]


@router.get('/previews/{preview_id}')
async def get_preview(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    preview = (await db.execute(select(Preview).where(Preview.id == preview_id, Preview.owner_id == user.id))).scalar_one_or_none()
    if not preview:
        raise HTTPException(404, 'Preview not found')
    return {'id': preview.id, 'document_id': preview.document_id, 'status': preview.status,
            'diff': preview.diff, 'operations': preview.operations, 'error': preview.error,
            'base_revision': preview.base_revision}


@router.get('/previews/{preview_id}/content')
async def get_preview_content(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    preview = (await db.execute(select(Preview).where(Preview.id == preview_id, Preview.owner_id == user.id))).scalar_one_or_none()
    if not preview or not preview.preview_key:
        raise HTTPException(404, 'Preview file not found')
    doc = await owned_document(db, preview.document_id, user.id)
    return Response(await ObjectStore().get(preview.preview_key), media_type=doc.mime_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote('preview-' + doc.filename)}"})


@router.post('/previews/{preview_id}/commit')
async def commit(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    revision, job = await commit_preview(db, ObjectStore(), user.id, preview_id)
    return {'revision': revision.number, 'sync_job_id': job.id, 'sync_status': job.status}


@router.post('/documents/{document_id}/sync/retry')
async def retry_sync(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await owned_document(db, document_id, user.id)
    job = (await db.execute(select(SyncJob).where(SyncJob.document_id == doc.id, SyncJob.status == 'failed')
                            .order_by(SyncJob.created_at).limit(1))).scalar_one_or_none()
    if not job or job.status != 'failed':
        raise HTTPException(409, 'No failed sync job to retry')
    if job.error and ('Drive conflict:' in job.error or 'no longer has edit access' in job.error):
        raise HTTPException(409, 'Reimport the Drive file or restore access before creating a new revision')
    job.status = 'pending'
    job.attempts = 0
    job.error = None
    await db.commit()
    return {'sync_job_id': job.id, 'status': job.status}


def _signer():
    secret = os.getenv('WEBUI_SECRET_KEY')
    if not secret:
        raise HTTPException(503, 'WEBUI_SECRET_KEY is required for OAuth')
    return URLSafeTimedSerializer(secret, salt='mini-hermes-drive-oauth')


@router.get('/drive/connect')
async def connect_drive(request: Request, user=Depends(get_verified_user)):
    nonce = secrets.token_urlsafe(24)
    state = _signer().dumps({'user_id': user.id, 'nonce': nonce})
    from fastapi.responses import JSONResponse

    response = JSONResponse({'authorization_url': authorization_url(state)})
    response.set_cookie('mh_drive_oauth_nonce', nonce, max_age=600, httponly=True,
                        samesite='lax', secure=request.url.scheme == 'https')
    return response


@router.get('/drive/callback')
async def drive_callback(request: Request, code: str, state: str, db=Depends(get_async_session)):
    try:
        signed = _signer().loads(state, max_age=600)
    except (BadSignature, SignatureExpired):
        raise HTTPException(400, 'Invalid OAuth state')
    if not secrets.compare_digest(signed['nonce'], request.cookies.get('mh_drive_oauth_nonce', '')):
        raise HTTPException(400, 'OAuth browser session mismatch')
    token = await exchange_code(code)
    if not token.get('refresh_token'):
        raise HTTPException(400, 'Google did not return an offline refresh token')
    await save_refresh_token(db, signed['user_id'], token['refresh_token'])
    response = RedirectResponse('/')
    response.delete_cookie('mh_drive_oauth_nonce')
    return response
