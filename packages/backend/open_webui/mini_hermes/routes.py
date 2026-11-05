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
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.chats import Chats
from open_webui.utils.auth import get_verified_user

from .drive import Gateway, NATIVE_TO_OFFICE
from .edits import normalize_operations
from .models import ChatResource, Document, Folder, Preview, Revision, SyncJob, now
from .oauth import access_token_for, authorization_url, exchange_code, save_refresh_token
from .service import MAX_BYTES, commit_preview, create_document, create_preview, owned_document, revision_for
from .storage import ObjectStore
from .library import (
    accessible_document,
    accessible_folder,
    expand_folder_documents,
    has_folder_access,
    has_document_access,
    list_personal_documents,
    list_chat_documents,
    chat_attachment_file_ids,
    move_document,
    register_chat_upload,
    set_chat_document_access,
    ensure_personal_folder,
)


router = APIRouter()


class LinkRequest(BaseModel):
    url: str


class PreviewRequest(BaseModel):
    operations: list[dict]


class FolderRequest(BaseModel):
    name: str
    parent_id: str | None = None


class ChatFilesRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list)
    folder_ids: list[str] = Field(default_factory=list)


class ChatAttachmentsRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


class AccessRequest(BaseModel):
    access_grants: list[dict] = Field(default_factory=list)


class MoveDocumentRequest(BaseModel):
    folder_id: str | None = None


def _user_id(user) -> str:
    return user.id


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
            'folder_id': doc.folder_id, 'description': doc.description,
            'description_status': doc.description_status, 'description_error': doc.description_error,
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
    docs = await list_personal_documents(db, user.id, user.role == 'admin')
    result = []
    for doc in docs:
        job = await _visible_sync_job(db, doc.id)
        result.append(_document_json(doc, job))
    return result


@router.get('/personal/files')
async def personal_files(chat_id: str | None = None, user=Depends(get_verified_user), db=Depends(get_async_session)):
    root = await ensure_personal_folder(db, user.id)
    if chat_id and not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    if chat_id:
        for file_id in await chat_attachment_file_ids(db, chat_id, user.id):
            document = await register_chat_upload(db, user.id, file_id, ObjectStore())
            if document:
                await set_chat_document_access(db, chat_id, user.id, document.id, True)
    folders = (await db.execute(select(Folder).order_by(Folder.name))).scalars().all()
    visible_folders = [folder for folder in folders if await has_folder_access(
        db, folder, user.id, 'read', is_admin=user.role == 'admin'
    )]
    docs = await list_personal_documents(db, user.id, user.role == 'admin')
    linked = set()
    if chat_id:
        linked = set((await db.execute(select(ChatResource.document_id).where(
            ChatResource.chat_id == chat_id
        ))).scalars().all())
    return {
        'root_folder_id': root.id,
        'folders': [{'id': folder.id, 'parent_id': folder.parent_id, 'name': folder.name,
                     'is_personal_root': folder.is_personal_root,
                     'can_write': await has_folder_access(
                         db, folder, user.id, 'write', is_admin=user.role == 'admin'
                     )} for folder in visible_folders],
        'documents': [{**_document_json(doc), 'in_chat': doc.id in linked,
                       'can_write': await has_document_access(
                           db, doc, user.id, 'write', is_admin=user.role == 'admin'
                       )} for doc in docs],
    }


@router.post('/folders')
async def create_folder(request: FolderRequest, user=Depends(get_verified_user), db=Depends(get_async_session)):
    parent_id = request.parent_id or (await ensure_personal_folder(db, user.id)).id
    parent = await accessible_folder(db, parent_id, user.id, 'write', is_admin=user.role == 'admin')
    folder = Folder(id=str(uuid4()), owner_id=user.id, parent_id=parent.id,
                    name=request.name.strip(), is_personal_root=False)
    if not folder.name or folder.name in {'.', '..'}:
        raise HTTPException(400, 'Folder name is required')
    db.add(folder)
    await db.commit()
    return {'id': folder.id, 'parent_id': folder.parent_id, 'name': folder.name}


@router.put('/documents/{document_id}/folder')
async def move_document_to_folder(document_id: str, request: MoveDocumentRequest,
                                  user=Depends(get_verified_user), db=Depends(get_async_session)):
    document = await move_document(db, document_id, user.id, request.folder_id, user.role == 'admin')
    return {'document_id': document.id, 'folder_id': document.folder_id}


@router.post('/folders/{folder_id}/access')
async def update_folder_access(folder_id: str, request: AccessRequest,
                               user=Depends(get_verified_user), db=Depends(get_async_session)):
    folder = await db.get(Folder, folder_id)
    if not folder or (folder.owner_id != user.id and user.role != 'admin'):
        raise HTTPException(404, 'Folder not found')
    _validate_grants(request.access_grants)
    grants = await AccessGrants.set_access_grants('mh_folder', folder.id, request.access_grants, db=db)
    return {'access_grants': [grant.model_dump() for grant in grants]}


@router.post('/documents/{document_id}/access')
async def update_document_access(document_id: str, request: AccessRequest,
                                 user=Depends(get_verified_user), db=Depends(get_async_session)):
    document = await db.get(Document, document_id)
    if not document or (document.owner_id != user.id and user.role != 'admin'):
        raise HTTPException(404, 'Document not found')
    _validate_grants(request.access_grants)
    grants = await AccessGrants.set_access_grants('mh_document', document.id, request.access_grants, db=db)
    return {'access_grants': [grant.model_dump() for grant in grants]}


def _validate_grants(grants: list[dict]) -> None:
    for grant in grants:
        if (grant.get('principal_type') not in {'user', 'group'} or not grant.get('principal_id')
                or grant.get('principal_id') == '*' or grant.get('permission') not in {'read', 'write'}):
            raise HTTPException(400, 'Use a user or group principal and read/write permission')


@router.put('/chats/{chat_id}/files')
async def update_chat_files(chat_id: str, request: ChatFilesRequest,
                            user=Depends(get_verified_user), db=Depends(get_async_session)):
    if not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    selected = {doc_id for doc_id in request.document_ids}
    for folder_id in set(request.folder_ids):
        await accessible_folder(db, folder_id, user.id, 'read', is_admin=user.role == 'admin')
        selected.update(doc.id for doc in await expand_folder_documents(db, folder_id))
    docs = []
    for document_id in selected:
        docs.append(await accessible_document(db, document_id, user.id, 'read', is_admin=user.role == 'admin'))
    await db.execute(delete(ChatResource).where(ChatResource.chat_id == chat_id))
    db.add_all([ChatResource(id=str(uuid4()), chat_id=chat_id, document_id=doc.id, granted_by=user.id) for doc in docs])
    await db.commit()
    return {'document_ids': sorted(selected)}


@router.put('/chats/{chat_id}/files/attachments')
async def register_chat_attachments(chat_id: str, request: ChatAttachmentsRequest,
                                    user=Depends(get_verified_user), db=Depends(get_async_session)):
    if not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    documents = []
    file_ids = dict.fromkeys([*request.file_ids, *await chat_attachment_file_ids(db, chat_id, user.id)])
    for file_id in file_ids:
        document = await register_chat_upload(db, user.id, file_id, ObjectStore())
        if document:
            await set_chat_document_access(db, chat_id, user.id, document.id, True)
            documents.append(document.id)
    return {'document_ids': documents}


@router.delete('/chats/{chat_id}/files/{document_id}')
async def remove_chat_file(chat_id: str, document_id: str,
                           user=Depends(get_verified_user), db=Depends(get_async_session)):
    if not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    row = (await db.execute(select(ChatResource).where(
        ChatResource.chat_id == chat_id, ChatResource.document_id == document_id
    ))).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {'removed': True}


@router.get('/chats/{chat_id}/previews')
async def list_chat_previews(chat_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    if not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    rows = (await db.execute(
        select(Preview, Document)
        .join(Document, Document.id == Preview.document_id)
        .join(ChatResource, ChatResource.document_id == Document.id)
        .where(
            ChatResource.chat_id == chat_id,
            Preview.owner_id == user.id,
        )
        .order_by(Preview.created_at.desc())
        .limit(10)
    )).all()
    result = []
    for preview, document in rows:
        committed = (await db.execute(select(Revision).where(Revision.preview_id == preview.id))).scalar_one_or_none()
        job = (await db.execute(select(SyncJob).where(SyncJob.revision_id == committed.id))).scalar_one_or_none() if committed else None
        url = (f'/api/v1/mini-hermes/documents/{document.id}/content?revision={committed.number}'
               if committed else None)
        result.append({'id': preview.id, 'document_id': document.id, 'filename': document.filename,
                       'status': preview.status, 'diff': preview.diff, 'error': preview.error,
                       'base_revision': preview.base_revision,
                       'revision': committed.number if committed else None,
                       'viewer_url': url, 'download_url': url,
                       'sync_status': job.status if job else 'local_only' if committed else None})
    return result


@router.get('/chats/{chat_id}/artifacts')
async def list_chat_artifacts(chat_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    """Return committed agent deliverables in this user's chat."""
    if not await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db):
        raise HTTPException(404, 'Chat not found')
    result = []
    for document in await list_chat_documents(db, chat_id, user.id, user.role == 'admin'):
        revision = await revision_for(db, document.id, document.current_revision)
        generated = bool(document.source_file_id and document.source_file_id.startswith('sandbox:'))
        edited = bool(revision.preview_id or any(
            isinstance(op, dict) and op.get('op') == 'sandbox_publish'
            for op in revision.operations or []))
        if not generated and not edited:
            continue
        url = f'/api/v1/mini-hermes/documents/{document.id}/content?revision={revision.number}'
        result.append({'document_id': document.id, 'filename': document.filename,
                       'revision': revision.number, 'status': 'committed',
                       'viewer_url': url, 'download_url': url, 'sync_status': 'local_only'})
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
    doc = await accessible_document(db, document_id, user.id, 'read', is_admin=user.role == 'admin')
    job = await _visible_sync_job(db, doc.id)
    return _document_json(doc, job)


@router.get('/source-files/{file_id}/document')
async def resolve_source_file(file_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = (await db.execute(select(Document).where(Document.source_file_id == file_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, 'Document not found')
    doc = await accessible_document(db, doc.id, user.id, 'read', is_admin=user.role == 'admin')
    return _document_json(doc)


@router.get('/documents/{document_id}/revisions')
async def list_document_revisions(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await accessible_document(db, document_id, user.id, 'read', is_admin=user.role == 'admin')
    records = (await db.execute(select(Revision).where(Revision.document_id == doc.id)
                                .order_by(Revision.number.desc()))).scalars().all()
    return [{'number': item.number, 'created_at': item.created_at.isoformat(),
             'size': item.size, 'current': item.number == doc.current_revision}
            for item in records]


@router.post('/documents/{document_id}/description/retry')
async def retry_description(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await accessible_document(db, document_id, user.id, 'write', is_admin=user.role == 'admin')
    doc.description = None
    doc.description_status = 'pending'
    doc.description_error = None
    doc.description_attempts = 0
    doc.description_next_attempt_at = now()
    await db.commit()
    return {'document_id': doc.id, 'description_status': doc.description_status}


@router.get('/documents/{document_id}/content')
async def get_content(document_id: str, revision: int | None = None,
                      user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await accessible_document(db, document_id, user.id, 'read', is_admin=user.role == 'admin')
    record = await revision_for(db, doc.id, revision or doc.current_revision)
    return Response(await ObjectStore().get(record.object_key), media_type=doc.mime_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(doc.filename)}"})


@router.post('/documents/{document_id}/previews')
async def request_preview(document_id: str, request: PreviewRequest,
                          user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await accessible_document(db, document_id, user.id, 'write', is_admin=user.role == 'admin')
    preview = await create_preview(db, doc, user.id, request.operations)
    return {'id': preview.id, 'status': preview.status}


@router.get('/documents/{document_id}/previews')
async def list_previews(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    await accessible_document(db, document_id, user.id, 'read', is_admin=user.role == 'admin')
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
    document = await db.get(Document, preview.document_id)
    return {'id': preview.id, 'document_id': preview.document_id, 'status': preview.status,
            'diff': preview.diff, 'operations': preview.operations, 'error': preview.error,
            'base_revision': preview.base_revision,
            'filename': document.filename if document else 'document'}


@router.get('/previews/{preview_id}/content')
async def get_preview_content(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    preview = (await db.execute(select(Preview).where(Preview.id == preview_id, Preview.owner_id == user.id))).scalar_one_or_none()
    if not preview or not preview.preview_key:
        raise HTTPException(404, 'Preview file not found')
    doc = await accessible_document(db, preview.document_id, user.id, 'read', is_admin=user.role == 'admin')
    return Response(await ObjectStore().get(preview.preview_key), media_type=doc.mime_type,
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote('preview-' + doc.filename)}"})


@router.post('/previews/{preview_id}/retry')
async def retry_preview(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    preview = (await db.execute(select(Preview).where(
        Preview.id == preview_id, Preview.owner_id == user.id
    ).with_for_update())).scalar_one_or_none()
    if not preview:
        raise HTTPException(404, 'Preview not found')
    if preview.status != 'failed':
        raise HTTPException(409, 'Only failed previews can be retried')
    doc = await accessible_document(db, preview.document_id, user.id, 'write', is_admin=user.role == 'admin')
    if doc.current_revision != preview.base_revision:
        raise HTTPException(409, 'Document changed since this preview was created; ask the agent to create a new preview')
    try:
        preview.operations = normalize_operations(preview.operations)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    preview.status = 'pending'
    preview.error = None
    preview.diff = None
    preview.preview_key = None
    preview.lease_until = None
    await db.commit()
    return {'id': preview.id, 'status': preview.status}


@router.post('/previews/{preview_id}/commit')
async def commit(preview_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    revision, job = await commit_preview(db, ObjectStore(), user.id, preview_id, user.role == 'admin')
    return {'revision': revision.number, 'sync_job_id': job.id if job else None,
            'sync_status': job.status if job else 'local_only'}


@router.post('/documents/{document_id}/sync/retry')
async def retry_sync(document_id: str, user=Depends(get_verified_user), db=Depends(get_async_session)):
    doc = await accessible_document(db, document_id, user.id, 'write', is_admin=user.role == 'admin')
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
