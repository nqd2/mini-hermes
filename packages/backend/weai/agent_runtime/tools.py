"""Agent-visible document tools, scoped to the current conversation."""

import base64
import hashlib
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from weai.internal.db import get_async_db
from weai.utils.chat_id import is_saved_chat_id

from weai.documents.edits import normalize_operations
from weai.documents.library import (
    accessible_document,
    chat_attachment_file_ids,
    document_in_chat_scope,
    list_chat_documents,
    set_chat_document_access,
)
from weai.documents.models import Document, ResourceTask
from weai.documents.resource_uploads import register_chat_upload
from .sandbox import SandboxClient
from .workspace_files import project_upload_ids, publish_workspace_file, raw_input_entries, raw_inputs
from weai.documents.service import build_preview, create_artifact_preview, create_preview, publish_generated_document
from weai.documents.storage import ObjectStore


def _file_ids(files: list[dict] | None) -> set[str]:
    return {
        str(item['id'])
        for item in files or []
        if isinstance(item, dict) and item.get('id') and item.get('type', 'file') in {'file', 'image'}
    }


async def list_editable_documents(
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> list[dict]:
    """List documents attached to or explicitly allowed in this chat."""
    if not __user__ or not __user__.get('id'):
        return []
    user_id = __user__['id']
    file_ids = _file_ids(__files__)
    async with get_async_db() as db:
        if is_saved_chat_id(__chat_id__):
            file_ids.update(await chat_attachment_file_ids(db, __chat_id__, user_id))
        attached: list[Document] = []
        store = ObjectStore()
        for file_id in file_ids:
            document = await register_chat_upload(db, user_id, file_id, store)
            if document:
                attached.append(document)
                if is_saved_chat_id(__chat_id__):
                    await set_chat_document_access(db, __chat_id__, user_id, document.id, True)

        docs = {doc.id: doc for doc in attached}
        if is_saved_chat_id(__chat_id__):
            for document in await list_chat_documents(db, __chat_id__, user_id, __user__.get('role') == 'admin'):
                docs[document.id] = document
        scope_ids = {str(file_id) for file_id in __resource_scope_ids__ or []}
        return [
            {
                'id': doc.id,
                'filename': doc.filename,
                'revision': doc.current_revision,
                'folder_id': doc.folder_id,
                'description': doc.description,
            }
            for doc in sorted(docs.values(), key=lambda item: item.filename.casefold())
            if not scope_ids or str(doc.source_file_id or doc.id) in scope_ids or doc.id in scope_ids
        ]


async def _scoped_document(
    db,
    document_id: str,
    user: dict,
    chat_id: str | None,
    files: list[dict] | None,
    permission: str,
    resource_scope_ids: list[str] | None = None,
) -> Document:
    doc = await accessible_document(db, document_id, user['id'], permission, is_admin=user.get('role') == 'admin')
    if not await document_in_chat_scope(db, doc, chat_id if is_saved_chat_id(chat_id) else None, _file_ids(files)):
        from fastapi import HTTPException

        raise HTTPException(404, 'Document is not available in this conversation')
    scope_ids = {str(file_id) for file_id in resource_scope_ids or []}
    if scope_ids and str(doc.source_file_id or doc.id) not in scope_ids and doc.id not in scope_ids:
        raise HTTPException(404, 'Document is outside the scope of this task')
    return doc


async def read_editable_document(
    document_id: str,
    offset: int = 0,
    limit: int = 20000,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    """Read a bounded text section of a document allowed in this conversation."""
    if not __user__:
        return {'error': 'User context unavailable'}
    if offset < 0 or limit < 1 or limit > 20000:
        return {'error': 'offset must be non-negative and limit must be between 1 and 20000'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'read', __resource_scope_ids__)
        from weai.documents.models import Revision

        revision = (
            await db.execute(
                select(Revision).where(Revision.document_id == doc.id, Revision.number == doc.current_revision)
            )
        ).scalar_one()
        from weai.documents.resource_service import ensure_revision_index

        _count, pending_job_id, failed_job_id = await ensure_revision_index(
            db,
            ObjectStore(),
            doc,
            revision,
        )
        if pending_job_id:
            return {
                'status': 'pending',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': revision.number,
                'index_job_ids': [pending_job_id],
                'coverage_complete': False,
                'evidence_ids': [],
            }
        if failed_job_id:
            return {
                'status': 'failed',
                'code': 'resource_index_failed',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': revision.number,
                'index_job_ids': [failed_job_id],
                'coverage_complete': False,
                'evidence_ids': [],
            }
        from weai.documents.chunks import document_range_evidence, read_document_range

        text, total_characters = await read_document_range(
            db,
            doc.id,
            revision.number,
            offset,
            limit,
        )
        return document_range_evidence(
            doc.id,
            doc.filename,
            revision.number,
            offset,
            text,
            total_characters,
        )


async def search_editable_document(
    document_id: str,
    query: str,
    limit: int = 8,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    """Search indexed document content and return versioned source evidence."""
    if not __user__:
        return {'error': 'User context unavailable'}
    if not query.strip() or limit < 1 or limit > 20:
        return {'error': 'query must be non-empty and limit must be between 1 and 20'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'read', __resource_scope_ids__)
        from weai.documents.chunks import search_document_chunks
        from weai.documents.models import Revision

        revision = (
            await db.execute(
                select(Revision).where(Revision.document_id == doc.id, Revision.number == doc.current_revision)
            )
        ).scalar_one()
        from weai.documents.resource_service import ensure_revision_index

        _count, pending_job_id, failed_job_id = await ensure_revision_index(
            db,
            ObjectStore(),
            doc,
            revision,
        )
        if pending_job_id:
            return {
                'status': 'pending',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': revision.number,
                'query': query,
                'index_job_ids': [pending_job_id],
                'coverage_complete': False,
                'search_complete': False,
                'evidence_ids': [],
                'results': [],
            }
        if failed_job_id:
            return {
                'status': 'failed',
                'code': 'resource_index_failed',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': revision.number,
                'query': query,
                'index_job_ids': [failed_job_id],
                'coverage_complete': False,
                'search_complete': False,
                'evidence_ids': [],
                'results': [],
            }
        matches = await search_document_chunks(db, doc.id, revision.number, query, limit)
        return {
            'status': 'succeeded',
            'document_id': doc.id,
            'filename': doc.filename,
            'revision': revision.number,
            'query': query,
            'coverage_complete': False,
            'search_complete': True,
            'resource_id': str(doc.source_file_id or doc.id),
            'searched_resource_ids': [str(doc.source_file_id or doc.id)],
            'evidence_ids': [chunk.id for chunk in matches],
            'results': [
                {
                    'evidence_id': chunk.id,
                    'section': chunk.section,
                    'page': chunk.page,
                    'start_offset': chunk.start_offset,
                    'end_offset': chunk.end_offset,
                    'metadata': chunk.metadata_json,
                    'text': chunk.content,
                }
                for chunk in matches
            ],
        }


async def start_document_summary_task(
    document_id: str,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __event_emitter__=None,
    __resource_scope_ids__: list[str] = None,
    __model__: dict | None = None,
) -> dict:
    """Start a resumable hierarchical summary for a scoped document version."""
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'read', __resource_scope_ids__)
        from .execution.contracts import ResourceIndexFailed, ResourceIndexPending
        from weai.documents.resource_tasks import create_summary_task

        try:
            task = await create_summary_task(
                db, doc, __user__['id'], __chat_id__, logical_model_id=(__model__ or {}).get('id'),
            )
        except ResourceIndexPending as exc:
            return {
                'status': 'pending',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': doc.current_revision,
                'index_job_ids': list(exc.job_ids),
            }
        except ResourceIndexFailed as exc:
            return {
                'status': 'failed',
                'code': 'resource_index_failed',
                'document_id': doc.id,
                'filename': doc.filename,
                'revision': doc.current_revision,
                'index_job_ids': list(exc.job_ids),
            }
        if __event_emitter__:
            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {
                        'description': f'Document summary task queued ({task.id})',
                        'done': False,
                    },
                }
            )
        return {
            'task_id': task.id,
            'status': task.status,
            'revision': task.revision,
            'status_endpoint': f'/api/v1/agent-runtime/tasks/{task.id}',
            'cancel_endpoint': f'/api/v1/agent-runtime/tasks/{task.id}/cancel',
            'resume_endpoint': f'/api/v1/agent-runtime/tasks/{task.id}/resume',
        }


async def get_document_task(
    task_id: str,
    evidence_offset: int = 0,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __event_emitter__=None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    """Read progress and a bounded evidence page for a document task."""
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        from weai.documents.resource_tasks import get_summary_task

        result = await get_summary_task(db, task_id, __user__['id'], evidence_offset=evidence_offset)
        doc = await _scoped_document(
            db, result['document_id'], __user__, __chat_id__, __files__, 'read', __resource_scope_ids__
        )
        result['filename'] = doc.filename
        result['resource_id'] = str(doc.source_file_id or doc.id)
        is_current_revision = result['revision'] == doc.current_revision
        result['coverage_complete'] = bool(
            is_current_revision
            and result['status'] == 'completed'
            and result.get('result')
            and result.get('evidence_ids')
        )
        if not is_current_revision:
            result.update(
                {
                    'status': 'failed',
                    'coverage_complete': False,
                    'evidence_ids': [],
                    'result': None,
                    'error': 'Document revision changed; this task is not evidence for the current version.',
                }
            )
        if __event_emitter__:
            progress = result['progress']
            await __event_emitter__(
                {
                    'type': 'status',
                    'data': {
                        'description': f'Document task {task_id}: {result["status"]} ({progress["completed_units"]}/{progress["total_units"]})',
                        'done': result['status'] in {'completed', 'failed', 'cancelled'},
                    },
                }
            )
        return result


async def cancel_document_task(
    task_id: str,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        task = (
            await db.execute(
                select(ResourceTask).where(ResourceTask.id == task_id, ResourceTask.owner_id == __user__['id'])
            )
        ).scalar_one_or_none()
        if not task:
            return {'error': 'Task not found'}
        await _scoped_document(db, task.document_id, __user__, __chat_id__, __files__, 'read', __resource_scope_ids__)
        from weai.documents.resource_tasks import request_task_cancel

        task = await request_task_cancel(db, task_id, __user__['id'])
        return {'task_id': task.id, 'status': task.status, 'cancel_requested': task.cancel_requested}


async def resume_document_task(
    task_id: str,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        task = (
            await db.execute(
                select(ResourceTask).where(ResourceTask.id == task_id, ResourceTask.owner_id == __user__['id'])
            )
        ).scalar_one_or_none()
        if not task:
            return {'error': 'Task not found'}
        doc = await _scoped_document(
            db, task.document_id, __user__, __chat_id__, __files__, 'read', __resource_scope_ids__
        )
        from weai.documents.resource_tasks import resume_summary_task

        task = await resume_summary_task(db, task_id, __user__['id'], doc)
        return {
            'task_id': task.id,
            'status': task.status,
            'completed_units': task.completed_units,
            'total_units': task.total_units,
        }


async def propose_document_edit(
    document_id: str,
    operations: list[dict],
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
) -> dict:
    """Create a staged edit preview; a user must review and approve its exact hashes before commit."""
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'write', __resource_scope_ids__)
        try:
            operations = normalize_operations(operations)
        except ValueError as exc:
            return {'error': str(exc)}
        preview = await create_preview(db, doc, __user__['id'], operations)
        store = ObjectStore()
        await build_preview(db, store, preview)
        if preview.status != 'ready':
            return {'error': preview.error or 'Document edit failed', 'preview_id': preview.id}
        return {
            'preview_id': preview.id,
            'status': 'ready',
            'document_id': doc.id,
            'base_revision': preview.base_revision,
            'base_sha256': preview.base_sha256,
            'artifact_sha256': preview.artifact_sha256,
            'patch_hash': preview.patch_hash,
            'diff': preview.diff,
            'approval_endpoint': f'/api/v1/agent-runtime/previews/{preview.id}/commit',
            'approval_required': True,
        }


def _workspace_id(user: dict, chat_id: str | None) -> str:
    if not chat_id:
        raise HTTPException(400, 'A chat identifier is required for a private workspace')
    return hashlib.sha256(f'{user["id"]}:{chat_id}'.encode()).hexdigest()


async def _sandbox_input_entries(
    db,
    user: dict,
    chat_id: str | None,
    files: list[dict] | None,
    resource_scope_ids: list[str] | None = None,
    *,
    folder_id: str | None = None,
) -> tuple[dict[str, int | str], dict[str, object], dict[str, str]]:
    file_ids = _file_ids(files)
    scope_ids = {str(file_id) for file_id in resource_scope_ids or []}
    scope_restricted = resource_scope_ids is not None
    if scope_restricted:
        file_ids.intersection_update(scope_ids)
    if is_saved_chat_id(chat_id):
        attached_ids = await chat_attachment_file_ids(db, chat_id, user['id'])
        if scope_restricted:
            attached_ids = [file_id for file_id in attached_ids if str(file_id) in scope_ids]
        file_ids.update(attached_ids)
    store = ObjectStore()
    docs: dict[str, Document] = {}
    for file_id in file_ids:
        document = await register_chat_upload(db, user['id'], file_id, store)
        if document:
            docs[document.id] = document
            if is_saved_chat_id(chat_id):
                await set_chat_document_access(db, chat_id, user['id'], document.id, True)
    if is_saved_chat_id(chat_id):
        for document in await list_chat_documents(db, chat_id, user['id'], user.get('role') == 'admin'):
            if (
                not scope_restricted
                or str(document.source_file_id or document.id) in scope_ids
                or document.id in scope_ids
            ):
                docs[document.id] = document
    project_ids = await project_upload_ids(db, folder_id, user)
    if scope_restricted:
        project_ids.intersection_update(scope_ids)
    raw_ids = file_ids - {doc.source_file_id for doc in docs.values()}
    if len(docs) + len(raw_ids | project_ids) > 30:
        raise HTTPException(413, 'Workspace exceeds 30 input files')
    remaining_bytes = 40 * 1024 * 1024
    input_revisions: dict[str, int | str] = {}
    loaders: dict[str, object] = {}
    for doc in sorted(docs.values(), key=lambda item: item.id):
        doc = await _scoped_document(db, doc.id, user, chat_id, files, 'read', resource_scope_ids)
        from weai.documents.models import Revision

        revision = (
            await db.execute(
                select(Revision).where(Revision.document_id == doc.id, Revision.number == doc.current_revision)
            )
        ).scalar_one()
        remaining_bytes -= revision.size
        if remaining_bytes < 0:
            raise HTTPException(413, 'Workspace inputs exceed 40 MB')
        name = Path(doc.filename).name
        input_path = f'inputs/{doc.id}/{name}'
        input_revisions[input_path] = revision.number

        async def _load_doc(rev=revision, s=store):
            from weai.documents.service import get_verified_revision_content

            return base64.b64encode(await get_verified_revision_content(s, rev)).decode()

        loaders[input_path] = _load_doc

    raw_revs, raw_loaders, prefetched = await raw_input_entries(
        db,
        user,
        raw_ids,
        project_ids,
        remaining_bytes=remaining_bytes,
        remaining_files=30 - len(input_revisions),
    )
    return {**input_revisions, **raw_revs}, {**loaders, **raw_loaders}, prefetched


async def _sandbox_inputs(
    db,
    user: dict,
    chat_id: str | None,
    files: list[dict] | None,
    resource_scope_ids: list[str] | None = None,
    *,
    folder_id: str | None = None,
) -> tuple[dict[str, str], dict[str, int | str]]:
    input_revisions, loaders, prefetched = await _sandbox_input_entries(
        db, user, chat_id, files, resource_scope_ids, folder_id=folder_id
    )
    inputs = dict(prefetched)
    for path, loader in loaders.items():
        inputs[path] = await loader()
    return inputs, input_revisions


def _build_input_evidence(input_revisions: dict[str, int | str]) -> list[dict]:
    from weai.documents.provenance import evidence_id

    input_evidence = []
    for input_path, revision in input_revisions.items():
        parts = PurePosixPath(input_path).parts
        if len(parts) < 3:
            continue
        document_id = parts[2] if len(parts) == 4 else parts[1]
        identifier = evidence_id(document_id, str(revision), None, None, 'sandbox-input')
        input_evidence.append(
            {
                'resource_id': document_id,
                'revision': revision,
                'input_path': input_path,
                'evidence_id': identifier,
            }
        )
    return input_evidence


async def _sync_workspace_context(
    client,
    user: dict,
    files: list[dict] | None,
    chat_id: str | None,
    resource_scope_ids: list[str] | None = None,
    folder_id: str | None = None,
) -> tuple[str, dict[str, int | str], dict[str, str]]:
    """Verify chat access and synchronize only missing/changed inputs into the sandbox."""
    workspace_id = _workspace_id(user, chat_id)
    async with get_async_db() as db:
        if is_saved_chat_id(chat_id):
            from weai.models.chats import Chats

            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user['id'], db=db)
            if not chat:
                raise HTTPException(404, 'Chat not found')
            folder_id = chat.folder_id
        input_revisions, loaders, prefetched = await _sandbox_input_entries(
            db, user, chat_id, files, resource_scope_ids, folder_id=folder_id
        )

    if hasattr(client, 'sync'):
        check = await client.sync(workspace_id, input_revisions, {})
        missing = list(check.get('missing_paths') or [])
        if not check.get('synced') or missing:
            delta_inputs: dict[str, str] = {}
            for path in missing:
                if path in prefetched:
                    delta_inputs[path] = prefetched[path]
                elif path in loaders:
                    delta_inputs[path] = await loaders[path]()
            await client.sync(workspace_id, input_revisions, delta_inputs)
        return workspace_id, input_revisions, {}

    eager_inputs = dict(prefetched)
    for path, loader in loaders.items():
        eager_inputs[path] = await loader()
    return workspace_id, input_revisions, eager_inputs


async def _execute_document(
    payload: dict,
    user: dict | None,
    files: list[dict] | None,
    chat_id: str | None,
    resource_scope_ids: list[str] | None = None,
    folder_id: str | None = None,
) -> dict:
    if not user or not user.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, input_revisions, eager_inputs = await _sync_workspace_context(
        client, user, files, chat_id, resource_scope_ids, folder_id=folder_id
    )
    payload.update(
        {'workspace_id': workspace_id, 'inputs': eager_inputs, 'input_revisions': input_revisions}
    )
    result = await client.run(payload)
    if isinstance(result, dict) and not result.get('error') and result.get('status') not in {'failed', 'error'}:
        input_evidence = _build_input_evidence(input_revisions)
        result['input_evidence'] = input_evidence
        result['evidence_ids'] = [item['evidence_id'] for item in input_evidence]
    return result


async def list_workspace_files(
    path: str = '.',
    max_depth: int = 3,
    limit: int = 200,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """List files and directories inside the private chat workspace (inputs/, work/, outputs/)."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, input_revisions, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    result = await client.fs_list(
        workspace_id, path=path, max_depth=max(1, min(max_depth, 10)), limit=max(1, min(limit, 500))
    )
    result['input_paths'] = sorted(input_revisions)
    return result


async def read_workspace_file(
    path: str,
    start_line: int = 1,
    end_line: int = 200,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Read a bounded text slice (with line numbers) or inspect a rendered image (PNG/JPG/WEBP) in the workspace."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, input_revisions, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    suffix = PurePosixPath(path).suffix.lower() if isinstance(path, str) else ''
    if suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
        image = await client.fs_read(workspace_id, path, as_image=True)
        mime_type = image.get('mime_type', 'image/png')
        content_b64 = image.get('content_b64', '')
        return {
            'path': image.get('path', path),
            'mime_type': mime_type,
            'size': image.get('size', 0),
            'image_url': f'data:{mime_type};base64,{content_b64}',
        }
    budget = int((__metadata__ or {}).get('resource_file_context_char_budget') or 16_000)
    max_chars = max(1_000, min(16_000, budget // 2))
    result = await client.fs_read(
        workspace_id,
        path,
        start_line=max(1, int(start_line)),
        end_line=max(1, int(end_line)),
        max_chars=max_chars,
    )
    input_evidence = _build_input_evidence(input_revisions)
    result['evidence_ids'] = [item['evidence_id'] for item in input_evidence]
    return result


async def write_workspace_file(
    path: str,
    content: str,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Create or overwrite a UTF-8 text file inside work/ or outputs/ (inputs/ is read-only)."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    if not isinstance(content, str):
        return {'error': 'content must be a UTF-8 string'}
    client = SandboxClient()
    workspace_id, _, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    return await client.fs_write(workspace_id, path, content)


async def edit_workspace_file(
    path: str,
    target: str,
    replacement: str,
    replace_all: bool = False,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Replace an exact text block inside an existing file under work/ or outputs/."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, _, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    return await client.fs_edit(workspace_id, path, target, replacement, replace_all=bool(replace_all))


async def search_workspace_files(
    pattern: str,
    path: str = 'work',
    glob: str | None = None,
    case_insensitive: bool = False,
    max_matches: int = 50,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Search text files inside inputs/, work/, or outputs/ using a regex or literal pattern."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, input_revisions, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    result = await client.fs_search(
        workspace_id,
        pattern,
        path=path,
        glob=glob,
        case_insensitive=bool(case_insensitive),
        max_matches=max(1, min(int(max_matches), 200)),
    )
    input_evidence = _build_input_evidence(input_revisions)
    result['evidence_ids'] = [item['evidence_id'] for item in input_evidence]
    result['search_complete'] = not bool(result.get('truncated'))
    return result


async def run_workspace_subtasks(
    instructions: str,
    tasks: list[dict] | None = None,
    input_glob: str | None = None,
    output_dir: str | None = None,
    manifest_path: str | None = None,
    report_path: str = 'work/subtasks_report.json',
    context_paths: list[str] | None = None,
    max_concurrency: int = 3,
    max_output_tokens: int = 8192,
    overwrite: bool = False,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
    __request__=None,
    __model__: dict | None = None,
    __event_emitter__=None,
) -> dict:
    """Delegate bounded LLM subtasks over chunk files in work/ (e.g., translating or transforming large documents/many files).
    Prefer passing input_glob (e.g. "work/ooxml/chunks/chunk_*.json") + output_dir (e.g. "work/ooxml/translated"),
    or manifest_path ("work/subtasks.json"), rather than listing dozens of items inline in tasks.
    Skips already-written outputs unless overwrite=True. Writes full details to report_path and returns a compact summary."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    client = SandboxClient()
    workspace_id, input_revisions, _ = await _sync_workspace_context(
        client, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    from .workspace_subtasks import execute_workspace_subtasks

    result = await execute_workspace_subtasks(
        client=client,
        workspace_id=workspace_id,
        user=__user__,
        instructions=instructions,
        tasks=tasks,
        input_glob=input_glob,
        output_dir=output_dir,
        manifest_path=manifest_path,
        report_path=report_path,
        context_paths=context_paths,
        max_concurrency=max_concurrency,
        max_output_tokens=max_output_tokens,
        overwrite=bool(overwrite),
        request=__request__,
        model=__model__,
        event_emitter=__event_emitter__,
    )
    if isinstance(result, dict) and not result.get('error'):
        input_evidence = _build_input_evidence(input_revisions)
        result['evidence_ids'] = [item['evidence_id'] for item in input_evidence]
    return result


async def run_workspace_command(
    command: str,
    timeout_seconds: int = 30,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Run a terminal command in the private chat workspace. Inspect inputs/, edit project files and scripts
    in work/, run tests, then save deliverables in outputs/. Returns exit code, stdout, stderr and output paths.
    """
    if not command.strip() or '\x00' in command or len(command) > 100_000:
        return {'error': 'command must be between 1 and 100000 characters'}
    if not 1 <= timeout_seconds <= 300:
        return {'error': 'timeout_seconds must be between 1 and 300'}
    result = await _execute_document(
        {'language': 'shell', 'code': command, 'timeout_seconds': timeout_seconds},
        __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )
    # Preserve the exit code and the end of a traceback in a bounded model observation.
    budget = int((__metadata__ or {}).get('resource_file_context_char_budget') or 16_000)
    stream_limit = max(100, min(4_000, budget // 5))
    for stream in ('stdout', 'stderr'):
        output = result.get(stream, '')
        if len(output) > stream_limit:
            half = stream_limit // 2
            result[stream] = output[:half] + '\n... output truncated ...\n' + output[-half:]
            result[f'{stream}_truncated'] = True
    return result


async def run_document_code(
    language: str,
    code: str,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Run Python or JavaScript in the document sandbox. Current working directory is the workspace root. Read files using relative paths under inputs/ (e.g. 'inputs/...', do NOT use root '/inputs/...'); write deliverables under outputs/."""
    if language not in {'python', 'javascript', 'node'}:
        return {'error': 'language must be python or javascript'}
    if not code or len(code) > 100_000:
        return {'error': 'code must be between 1 and 100000 characters'}
    return await _execute_document(
        {'language': language, 'code': code}, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )


async def run_skill_script(
    skill: str,
    script: str,
    args: list[str] | None = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Run a bundled document skill script in the sandbox using conversation files."""
    if skill not in {'docx', 'xlsx', 'pptx', 'pdf'}:
        return {'error': 'Unknown document skill'}
    return await _execute_document(
        {'skill': skill, 'script': script, 'args': args or []}, __user__, __files__, __chat_id__, __resource_scope_ids__,
        folder_id=(__metadata__ or {}).get('folder_id'),
    )


async def read_document_skill(
    skill: str,
    path: str = 'SKILL.md',
    offset: int = 0,
    limit: int = 4000,
    __user__: dict = None,
) -> dict:
    """Read bundled skill instructions or referenced Markdown files in bounded sections."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    if skill not in {'docx', 'xlsx', 'pptx', 'pdf'} or offset < 0 or not 1 <= limit <= 20000:
        return {'error': 'Invalid skill or range'}
    relative = PurePosixPath(path)
    if relative.is_absolute() or '..' in relative.parts or '\\' in path or relative.suffix.lower() != '.md':
        return {'error': 'Only skill Markdown files may be read'}
    root = Path(__file__).parent / 'skills' / skill
    target = root.joinpath(*relative.parts)
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        return {'error': 'Skill file not found'}
    content = target.read_text(encoding='utf-8')
    end = min(offset + limit, len(content))
    return {
        'skill': skill,
        'path': path,
        'offset': offset,
        'next_offset': end if end < len(content) else None,
        'total_characters': len(content),
        'text': content[offset:end],
    }


async def publish_document_output(
    path: str,
    source_document_id: str | None = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __chat_id__: str = None,
    __resource_scope_ids__: list[str] = None,
    __metadata__: dict | None = None,
) -> dict:
    """Save a new output, or stage an update to an existing document for user approval."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    relative = PurePosixPath(path)
    if (
        not relative.parts
        or relative.is_absolute()
        or '..' in relative.parts
        or '\\' in path
        or relative.parts[0] != 'outputs'
        or len(relative.parts) < 2
    ):
        return {'error': 'path must be under outputs/'}
    workspace_id = _workspace_id(__user__, __chat_id__)
    async with get_async_db() as db:
        if is_saved_chat_id(__chat_id__):
            from weai.models.chats import Chats

            chat = await Chats.get_chat_by_id_and_user_id(__chat_id__, __user__['id'], db=db)
            if not chat:
                raise HTTPException(404, 'Chat not found')
        source_document = None
        if source_document_id:
            source_document = await _scoped_document(
                db, source_document_id, __user__, __chat_id__, __files__, 'write', __resource_scope_ids__
            )
            if PurePosixPath(source_document.filename).suffix.lower() != relative.suffix.lower():
                raise HTTPException(400, 'Output format must match source document')
        output = await SandboxClient().read(workspace_id, path)
        input_paths = output.get('input_paths')
        input_revisions = output.get('input_revisions')
        if not isinstance(input_paths, list) or not isinstance(input_revisions, dict):
            raise HTTPException(503, 'Sandbox output provenance is unavailable')
        if set(input_paths) != set(input_revisions):
            raise HTTPException(503, 'Sandbox output provenance is invalid')
        raw_paths = {p for p in input_paths if isinstance(p, str) and len(PurePosixPath(p).parts) == 4}
        if not all(PurePosixPath(p).parts[:2] in {('inputs', 'project'), ('inputs', 'uploads')}
                   and isinstance(input_revisions[p], str) for p in raw_paths):
            raise HTTPException(503, 'Sandbox output provenance is invalid')
        if raw_paths:
            folder_id = chat.folder_id if is_saved_chat_id(__chat_id__) else (__metadata__ or {}).get('folder_id')
            requested_ids = {PurePosixPath(p).parts[2] for p in raw_paths}
            if __resource_scope_ids__ is not None:
                requested_ids.intersection_update(__resource_scope_ids__)
            attached = _file_ids(__files__)
            if is_saved_chat_id(__chat_id__):
                attached.update(await chat_attachment_file_ids(db, __chat_id__, __user__['id']))
            project = await project_upload_ids(db, folder_id, __user__)
            _, current = await raw_inputs(
                db, __user__, requested_ids & attached, requested_ids & project,
                remaining_bytes=40 * 1024 * 1024, remaining_files=30,
            )
            if any(p not in current or current[p] != input_revisions[p] for p in raw_paths):
                raise HTTPException(409, 'Workspace inputs changed or are no longer available')
        for input_path in set(input_paths) - raw_paths:
            parts = PurePosixPath(input_path).parts if isinstance(input_path, str) else ()
            if len(parts) != 3 or parts[0] != 'inputs' or type(input_revisions[input_path]) is not int:
                raise HTTPException(503, 'Sandbox output provenance is invalid')
            input_document = await _scoped_document(
                db, parts[1], __user__, __chat_id__, __files__, 'read', __resource_scope_ids__
            )
            if input_document.current_revision != input_revisions[input_path]:
                raise HTTPException(409, 'Document changed since the sandbox input was prepared')
        if source_document_id and source_document_id not in {
            PurePosixPath(input_path).parts[1] for input_path in input_paths
        }:
            raise HTTPException(409, 'Source document was not included in the sandbox run')
        content = base64.b64decode(output['content_b64'], validate=True)
        if source_document:
            preview = await create_artifact_preview(db, ObjectStore(), source_document, __user__['id'], content)
            if preview.status != 'ready':
                return {'error': preview.error or 'Document preview failed', 'preview_id': preview.id}
            return {
                'preview_id': preview.id,
                'status': 'ready',
                'document_id': source_document.id,
                'base_revision': preview.base_revision,
                'base_sha256': preview.base_sha256,
                'artifact_sha256': preview.artifact_sha256,
                'patch_hash': preview.patch_hash,
                'diff': preview.diff,
                'preview_url': f'/api/v1/agent-runtime/previews/{preview.id}/content',
                'approval_endpoint': f'/api/v1/agent-runtime/previews/{preview.id}/commit',
                'approval_required': True,
            }
        from weai.documents.library import SUPPORTED_RESOURCE_SUFFIXES

        if relative.suffix.lower() not in SUPPORTED_RESOURCE_SUFFIXES:
            if not content:
                raise HTTPException(422, 'Workspace output is empty')
            return await publish_workspace_file(db, __user__['id'], workspace_id, path, content)
        doc, revision, job = await publish_generated_document(
            db,
            ObjectStore(),
            __user__['id'],
            path,
            content,
            f'{workspace_id}:{path}',
        )
        if is_saved_chat_id(__chat_id__) and not source_document_id:
            await set_chat_document_access(db, __chat_id__, __user__['id'], doc.id, True)
    url = f'/api/v1/agent-runtime/documents/{doc.id}/content?revision={revision.number}'
    return {
        'document_id': doc.id,
        'filename': doc.filename,
        'revision': revision.number,
        'viewer_url': url,
        'download_url': url,
        'sync_status': job.status if job else 'local_only',
    }
