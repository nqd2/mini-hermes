"""Personal folders, document ACLs and conversation-scoped references."""

from __future__ import annotations

import asyncio
from uuid import uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups

from .models import ChatResource, Document, Folder


async def chat_attachment_file_ids(db: AsyncSession, chat_id: str, user_id: str) -> list[str]:
    """Return file ids attached to messages in a chat owned by this user."""
    from open_webui.models.chats import Chats, chat_message_list

    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id, db=db)
    if not chat:
        return []
    found: dict[str, None] = {}
    for message in chat_message_list(chat.chat):
        if not isinstance(message, dict):
            continue
        attachments = message.get('files') or []
        if isinstance(attachments, dict):
            attachments = [attachments]
        for attachment in attachments:
            if (isinstance(attachment, dict) and attachment.get('id')
                    and attachment.get('type', 'file') == 'file'):
                found[str(attachment['id'])] = None
    return list(found)


def _groups_for(db: AsyncSession, user_id: str):
    return Groups.get_groups_by_member_id(user_id, db=db, include_inherited=True)


async def ensure_personal_folder(db: AsyncSession, user_id: str) -> Folder:
    """Return the stable personal root for a user, creating it on first use."""
    root_id = str(uuid5(NAMESPACE_URL, f'mini-hermes:personal:{user_id}'))
    root = await db.get(Folder, root_id)
    if root:
        return root
    root = Folder(id=root_id, owner_id=user_id, parent_id=None, name='Personal', is_personal_root=True)
    db.add(root)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        root = await db.get(Folder, root_id)
        if root:
            return root
        raise
    await db.refresh(root)
    return root


async def has_document_access(
    db: AsyncSession, document: Document, user_id: str, permission: str = 'read',
    user_group_ids: set[str] | None = None, is_admin: bool = False,
) -> bool:
    if is_admin or document.owner_id == user_id:
        return True
    if user_group_ids is None:
        user_group_ids = {group.id for group in await _groups_for(db, user_id)}
    if await AccessGrants.has_access(
        user_id, 'mh_document', document.id, permission, user_group_ids=user_group_ids, db=db
    ):
        return True
    folder_id = document.folder_id
    seen: set[str] = set()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        folder = await db.get(Folder, folder_id)
        if not folder:
            break
        if folder.owner_id == user_id:
            return True
        if await AccessGrants.has_access(
            user_id, 'mh_folder', folder.id, permission, user_group_ids=user_group_ids, db=db
        ):
            return True
        folder_id = folder.parent_id
    return False


async def has_folder_access(
    db: AsyncSession, folder: Folder, user_id: str, permission: str = 'read',
    user_group_ids: set[str] | None = None, is_admin: bool = False,
) -> bool:
    if is_admin or folder.owner_id == user_id:
        return True
    if user_group_ids is None:
        user_group_ids = {group.id for group in await _groups_for(db, user_id)}
    current: Folder | None = folder
    seen: set[str] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        if current.owner_id == user_id or await AccessGrants.has_access(
            user_id, 'mh_folder', current.id, permission, user_group_ids=user_group_ids, db=db
        ):
            return True
        current = await db.get(Folder, current.parent_id) if current.parent_id else None
    return False


async def accessible_folder(
    db: AsyncSession, folder_id: str, user_id: str, permission: str = 'read', is_admin: bool = False,
) -> Folder:
    folder = await db.get(Folder, folder_id)
    if not folder or not await has_folder_access(db, folder, user_id, permission, is_admin=is_admin):
        raise HTTPException(404, 'Folder not found')
    return folder


async def accessible_document(
    db: AsyncSession, document_id: str, user_id: str, permission: str = 'read',
    user_group_ids: set[str] | None = None, is_admin: bool = False, lock: bool = False,
) -> Document:
    if lock:
        document = (await db.execute(select(Document).where(Document.id == document_id).with_for_update())).scalar_one_or_none()
    else:
        document = await db.get(Document, document_id)
    if not document or not await has_document_access(
        db, document, user_id, permission, user_group_ids, is_admin
    ):
        raise HTTPException(404, 'Document not found')
    return document


async def list_personal_documents(db: AsyncSession, user_id: str, is_admin: bool = False) -> list[Document]:
    docs = (await db.execute(select(Document).order_by(Document.filename))).scalars().all()
    group_ids = {group.id for group in await _groups_for(db, user_id)}
    return [doc for doc in docs if await has_document_access(db, doc, user_id, 'read', group_ids, is_admin)]


async def set_chat_document_access(
    db: AsyncSession, chat_id: str, user_id: str, document_id: str, allowed: bool,
    is_admin: bool = False,
) -> None:
    document = await accessible_document(db, document_id, user_id, 'read', is_admin=is_admin)
    row = (await db.execute(select(ChatResource).where(
        ChatResource.chat_id == chat_id, ChatResource.document_id == document.id
    ))).scalar_one_or_none()
    if allowed and row is None:
        db.add(ChatResource(id=str(uuid4()), chat_id=chat_id, document_id=document.id, granted_by=user_id))
    elif not allowed and row is not None:
        await db.delete(row)
    await db.commit()


async def add_chat_document_access(
    db: AsyncSession, chat_id: str, user_id: str, document_id: str,
) -> None:
    await set_chat_document_access(db, chat_id, user_id, document_id, True)


async def document_in_chat_scope(
    db: AsyncSession, document: Document, chat_id: str | None, attached_file_ids: set[str],
) -> bool:
    if chat_id:
        return bool(await db.scalar(select(ChatResource.id).where(
            ChatResource.chat_id == chat_id, ChatResource.document_id == document.id
        )))
    return bool(document.source_file_id and document.source_file_id in attached_file_ids)


async def list_chat_documents(
    db: AsyncSession, chat_id: str, user_id: str, is_admin: bool = False,
) -> list[Document]:
    rows = (await db.execute(select(ChatResource).where(ChatResource.chat_id == chat_id))).scalars().all()
    docs: list[Document] = []
    for row in rows:
        document = await db.get(Document, row.document_id)
        if document and await has_document_access(db, document, user_id, 'read', is_admin=is_admin):
            docs.append(document)
    return docs


async def expand_folder_documents(db: AsyncSession, folder_id: str) -> list[Document]:
    """Snapshot current documents below a folder, including nested descendants."""
    folder_ids = {folder_id}
    changed = True
    while changed:
        children = (await db.execute(select(Folder.id).where(Folder.parent_id.in_(folder_ids)))).scalars().all()
        new_ids = set(children) - folder_ids
        changed = bool(new_ids)
        folder_ids.update(new_ids)
    return (await db.execute(select(Document).where(Document.folder_id.in_(folder_ids)))).scalars().all()


async def move_document(
    db: AsyncSession, document_id: str, user_id: str, folder_id: str | None, is_admin: bool = False,
) -> Document:
    document = await accessible_document(db, document_id, user_id, 'write', is_admin=is_admin)
    target = await ensure_personal_folder(db, user_id) if folder_id is None else await accessible_folder(
        db, folder_id, user_id, 'write', is_admin=is_admin
    )
    document.folder_id = target.id
    await db.commit()
    return document


async def register_chat_upload(db: AsyncSession, user_id: str, file_id: str, store) -> Document | None:
    """Idempotently copy an Office attachment into the user's immutable document library."""
    from pathlib import Path

    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    existing = (await db.execute(select(Document).where(Document.source_file_id == file_id))).scalar_one_or_none()
    if existing:
        if existing.owner_id != user_id:
            return None
        return existing
    uploaded = await Files.get_file_by_id_and_user_id(file_id, user_id, db=db)
    if not uploaded:
        return None
    metadata = uploaded.meta if isinstance(uploaded.meta, dict) else {}
    filename = Path(metadata.get('name') or uploaded.filename).name
    if Path(filename).suffix.lower() not in {'.docx', '.xlsx', '.pptx', '.pdf'}:
        return None
    if not uploaded.path:
        raise HTTPException(422, 'Uploaded document has no stored file path')

    def read_source() -> bytes:
        local_path = Storage.get_file(uploaded.path)
        with open(local_path, 'rb') as source:
            return source.read()

    content = await asyncio.to_thread(read_source)
    from .service import create_document

    root = await ensure_personal_folder(db, user_id)
    return await create_document(
        db, store, user_id, filename, content, folder_id=root.id, source_file_id=file_id
    )
