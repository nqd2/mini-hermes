"""Agent-visible document tools, scoped to the current conversation."""

import base64
import hashlib
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from open_webui.internal.db import get_async_db
from open_webui.utils.chat_id import is_saved_chat_id

from .edits import extract_text, normalize_operations
from .library import (
    accessible_document,
    document_in_chat_scope,
    list_chat_documents,
    register_chat_upload,
    chat_attachment_file_ids,
    set_chat_document_access,
)
from .models import Document
from .sandbox import SandboxClient
from .service import build_preview, commit_preview, create_preview, publish_generated_document
from .storage import ObjectStore

def _file_ids(files: list[dict] | None) -> set[str]:
    return {
        str(item['id']) for item in files or []
        if isinstance(item, dict) and item.get('id') and item.get('type', 'file') == 'file'
    }


async def list_editable_documents(
    __user__: dict = None, __files__: list[dict] = None, __chat_id__: str = None,
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
        return [
            {
                'id': doc.id,
                'filename': doc.filename,
                'revision': doc.current_revision,
                'folder_id': doc.folder_id,
                'description': doc.description,
            }
            for doc in sorted(docs.values(), key=lambda item: item.filename.casefold())
        ]


async def _scoped_document(
    db, document_id: str, user: dict, chat_id: str | None, files: list[dict] | None, permission: str,
) -> Document:
    doc = await accessible_document(
        db, document_id, user['id'], permission, is_admin=user.get('role') == 'admin'
    )
    if not await document_in_chat_scope(db, doc, chat_id, _file_ids(files)):
        from fastapi import HTTPException

        raise HTTPException(404, 'Document is not available in this conversation')
    return doc


async def read_editable_document(
    document_id: str, offset: int = 0, limit: int = 20000,
    __user__: dict = None, __files__: list[dict] = None, __chat_id__: str = None,
) -> dict:
    """Read a bounded text section of a document allowed in this conversation."""
    if not __user__:
        return {'error': 'User context unavailable'}
    if offset < 0 or limit < 1 or limit > 20000:
        return {'error': 'offset must be non-negative and limit must be between 1 and 20000'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'read')
        from .models import Revision

        revision = (await db.execute(select(Revision).where(
            Revision.document_id == doc.id, Revision.number == doc.current_revision
        ))).scalar_one()
        content = await ObjectStore().get(revision.object_key)
        text = extract_text(content, doc.filename)
        end = min(offset + limit, len(text))
        return {
            'document_id': doc.id,
            'filename': doc.filename,
            'revision': revision.number,
            'offset': offset,
            'next_offset': end if end < len(text) else None,
            'total_characters': len(text),
            'text': text[offset:end],
        }


async def propose_document_edit(
    document_id: str, operations: list[dict], __user__: dict = None,
    __files__: list[dict] = None, __chat_id__: str = None,
) -> dict:
    """Apply document operations and auto-save a revision. Use {"op":"replace_text","find":"old text","replace":"new text"} for DOCX or PPTX text changes."""
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        doc = await _scoped_document(db, document_id, __user__, __chat_id__, __files__, 'write')
        try:
            operations = normalize_operations(operations)
        except ValueError as exc:
            return {'error': str(exc)}
        preview = await create_preview(db, doc, __user__['id'], operations)
        store = ObjectStore()
        await build_preview(db, store, preview)
        if preview.status != 'ready':
            return {'error': preview.error or 'Document edit failed', 'preview_id': preview.id}
        revision, job = await commit_preview(
            db, store, __user__['id'], preview.id, __user__.get('role') == 'admin', sync=False)
        url = f'/api/v1/mini-hermes/documents/{doc.id}/content?revision={revision.number}'
        return {'preview_id': preview.id, 'status': 'committed', 'document_id': doc.id,
                'revision': revision.number, 'viewer_url': url, 'download_url': url,
                'sync_status': job.status if job else 'local_only'}


def _workspace_id(user: dict, chat_id: str | None, files: list[dict] | None) -> str:
    scope = chat_id or ','.join(sorted(_file_ids(files)))
    return hashlib.sha256(f'{user["id"]}:{scope}'.encode()).hexdigest()


async def _sandbox_inputs(db, user: dict, chat_id: str | None, files: list[dict] | None) -> tuple[dict[str, str], dict[str, int]]:
    file_ids = _file_ids(files)
    if is_saved_chat_id(chat_id):
        file_ids.update(await chat_attachment_file_ids(db, chat_id, user['id']))
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
            docs[document.id] = document
    inputs = {}
    input_revisions = {}
    for doc in sorted(docs.values(), key=lambda item: item.id):
        doc = await _scoped_document(db, doc.id, user, chat_id, files, 'read')
        from .models import Revision

        revision = (await db.execute(select(Revision).where(
            Revision.document_id == doc.id, Revision.number == doc.current_revision
        ))).scalar_one()
        name = Path(doc.filename).name
        input_path = f'inputs/{doc.id}/{name}'
        inputs[input_path] = base64.b64encode(await store.get(revision.object_key)).decode()
        input_revisions[input_path] = revision.number
    return inputs, input_revisions


async def _execute_document(
    payload: dict, user: dict | None, files: list[dict] | None, chat_id: str | None,
) -> dict:
    if not user or not user.get('id'):
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        if is_saved_chat_id(chat_id):
            from open_webui.models.chats import Chats

            if not await Chats.get_chat_by_id_and_user_id(chat_id, user['id'], db=db):
                raise HTTPException(404, 'Chat not found')
        inputs, input_revisions = await _sandbox_inputs(db, user, chat_id, files)
    payload.update({'workspace_id': _workspace_id(user, chat_id, files), 'inputs': inputs,
                    'input_revisions': input_revisions})
    return await SandboxClient().run(payload)


async def run_document_code(
    language: str, code: str, __user__: dict = None,
    __files__: list[dict] = None, __chat_id__: str = None,
) -> dict:
    """Run Python or JavaScript in the document sandbox. Read files under inputs/; write deliverables under outputs/."""
    if language not in {'python', 'javascript', 'node'}:
        return {'error': 'language must be python or javascript'}
    if not code or len(code) > 100_000:
        return {'error': 'code must be between 1 and 100000 characters'}
    return await _execute_document({'language': language, 'code': code}, __user__, __files__, __chat_id__)


async def run_skill_script(
    skill: str, script: str, args: list[str] | None = None, __user__: dict = None,
    __files__: list[dict] = None, __chat_id__: str = None,
) -> dict:
    """Run a bundled document skill script in the sandbox using conversation files."""
    if skill not in {'docx', 'xlsx', 'pptx', 'pdf'}:
        return {'error': 'Unknown document skill'}
    return await _execute_document({'skill': skill, 'script': script, 'args': args or []},
                                   __user__, __files__, __chat_id__)


async def read_document_skill(
    skill: str, path: str = 'SKILL.md', offset: int = 0, limit: int = 16000,
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
    return {'skill': skill, 'path': path, 'offset': offset, 'next_offset': end if end < len(content) else None,
            'total_characters': len(content), 'text': content[offset:end]}


async def publish_document_output(
    path: str, source_document_id: str | None = None, __user__: dict = None,
    __files__: list[dict] = None, __chat_id__: str = None,
) -> dict:
    """Save a sandbox output as a Personal document, or commit it as a source document revision."""
    if not __user__ or not __user__.get('id'):
        return {'error': 'User context unavailable'}
    relative = PurePosixPath(path)
    if (not relative.parts or relative.is_absolute() or '..' in relative.parts
            or '\\' in path or relative.parts[0] != 'outputs' or len(relative.parts) < 2):
        return {'error': 'path must be under outputs/'}
    workspace_id = _workspace_id(__user__, __chat_id__, __files__)
    async with get_async_db() as db:
        if is_saved_chat_id(__chat_id__):
            from open_webui.models.chats import Chats

            if not await Chats.get_chat_by_id_and_user_id(__chat_id__, __user__['id'], db=db):
                raise HTTPException(404, 'Chat not found')
        if source_document_id:
            await _scoped_document(db, source_document_id, __user__, __chat_id__, __files__, 'write')
        output = await SandboxClient().read(workspace_id, path)
        input_paths = output.get('input_paths')
        input_revisions = output.get('input_revisions')
        if not isinstance(input_paths, list) or not isinstance(input_revisions, dict):
            raise HTTPException(503, 'Sandbox output provenance is unavailable')
        if set(input_paths) != set(input_revisions):
            raise HTTPException(503, 'Sandbox output provenance is invalid')
        for input_path in input_paths:
            parts = PurePosixPath(input_path).parts if isinstance(input_path, str) else ()
            if len(parts) < 3 or parts[0] != 'inputs':
                raise HTTPException(503, 'Sandbox output provenance is invalid')
            input_document = await _scoped_document(db, parts[1], __user__, __chat_id__, __files__, 'read')
            if input_document.current_revision != input_revisions[input_path]:
                raise HTTPException(409, 'Document changed since the sandbox input was prepared')
        if source_document_id and source_document_id not in {
            PurePosixPath(input_path).parts[1] for input_path in input_paths
        }:
            raise HTTPException(409, 'Source document was not included in the sandbox run')
        content = base64.b64decode(output['content_b64'], validate=True)
        doc, revision, job = await publish_generated_document(
            db, ObjectStore(), __user__['id'], path, content, f'{workspace_id}:{path}',
            source_document_id=source_document_id, is_admin=__user__.get('role') == 'admin',
            expected_revision=next((input_revisions[input_path] for input_path in input_paths
                                    if PurePosixPath(input_path).parts[1] == source_document_id), None))
        if is_saved_chat_id(__chat_id__) and not source_document_id:
            await set_chat_document_access(db, __chat_id__, __user__['id'], doc.id, True)
    url = f'/api/v1/mini-hermes/documents/{doc.id}/content?revision={revision.number}'
    return {'document_id': doc.id, 'filename': doc.filename, 'revision': revision.number,
            'viewer_url': url, 'download_url': url, 'sync_status': job.status if job else 'local_only'}
