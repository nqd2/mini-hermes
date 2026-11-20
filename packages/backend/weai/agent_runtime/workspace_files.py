"""Bridge authorized chat/project uploads and downloadable workspace files."""

import asyncio
import base64
import hashlib
import io
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException

from weai.models.files import FileForm, Files
from weai.storage.provider import Storage


async def project_upload_ids(db, folder_id, user):
    if not folder_id:
        return set()
    from weai.models.folders import Folders
    from weai.utils.access_control.files import get_owner_accessible_folder_files
    from weai.utils.access_control.folders import has_folder_access

    folder = await Folders.get_folder_by_id(folder_id, db=db)
    if not folder or not (user.get('role') == 'admin' or await has_folder_access(user['id'], folder, 'read', db)):
        return set()
    entries = await get_owner_accessible_folder_files(folder, db=db)
    return {entry['id'] for entry in entries if entry.get('type') == 'file'}


async def raw_inputs(db, user, attachment_ids, project_ids, *, remaining_bytes, remaining_files):
    """Use storage-backed bytes. Never accept a local path supplied by a tool or chat."""
    inputs, revisions = {}, {}
    for file_id in sorted(attachment_ids | project_ids):
        file = await Files.get_file_by_id_without_content(file_id, db=db)
        if not file or not file.path or (file_id not in project_ids and file.user_id != user['id']):
            continue
        name = Path(file.filename).name
        if not name or name in {'.', '..'} or '/' in file_id or '\\' in file_id or '\\' in name:
            raise HTTPException(422, 'Invalid workspace input name')
        group = 'project' if file_id in project_ids else 'uploads'
        path = f'inputs/{group}/{file_id}/{name}'
        if len(inputs) >= remaining_files:
            raise HTTPException(413, 'Workspace exceeds 30 input files')

        def read_bytes():
            with open(Storage.get_file(file.path), 'rb') as stream:
                return stream.read(remaining_bytes + 1)

        content = await asyncio.to_thread(read_bytes)
        remaining_bytes -= len(content)
        if remaining_bytes < 0:
            raise HTTPException(413, 'Workspace inputs exceed 40 MB')
        inputs[path] = base64.b64encode(content).decode()
        revisions[path] = hashlib.sha256(content).hexdigest()
    return inputs, revisions


async def publish_workspace_file(db, user_id, workspace_id, path, content):
    """Publish an opaque download through the existing owner-checked Files API."""
    digest = hashlib.sha256(content).hexdigest()
    file_id = str(uuid5(NAMESPACE_URL, f'weai:workspace:{user_id}:{workspace_id}:{path}:{digest}'))
    name = Path(path).name
    existing = await Files.get_file_by_id_without_content(file_id, db=db)
    if not existing:
        # Distinct storage keys keep concurrent writers away from a committed file.
        _, key = await asyncio.to_thread(Storage.upload_file, io.BytesIO(content), str(uuid4()), {})
        form = FileForm(id=file_id, filename=name, path=key, hash=digest,
                        meta={'name': name, 'content_type': 'application/octet-stream', 'size': len(content)})
        saved = await Files.insert_new_file(user_id, form, db=db)
        if not saved:
            await db.rollback()
            saved = await Files.get_file_by_id_without_content(file_id, db=db)
            if not saved or saved.path != key:
                await asyncio.to_thread(Storage.delete_file, key)
            if not saved:
                raise HTTPException(503, 'Could not publish workspace file')
        existing = saved
    if existing.user_id != user_id or existing.hash != digest:
        raise HTTPException(409, 'Workspace file publication conflict')
    return {'file_id': file_id, 'filename': name, 'sha256': digest,
            'download_url': f'/api/v1/files/{file_id}/content?attachment=true'}
