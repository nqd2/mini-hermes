"""Open WebUI upload adapters that adopt chat attachments as resources."""

import asyncio
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .library import SUPPORTED_RESOURCE_SUFFIXES, ensure_personal_folder
from .models import Document
from .service import create_document


async def register_chat_upload(db: AsyncSession, user_id: str, file_id: str, store):
    """Idempotently copy an Office attachment into the user's immutable document library."""
    existing = (await db.execute(select(Document).where(Document.source_file_id == file_id))).scalar_one_or_none()
    if existing:
        return existing if existing.owner_id == user_id else None

    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    uploaded = await Files.get_file_by_id_and_user_id(file_id, user_id, db=db)
    if not uploaded:
        return None

    metadata = uploaded.meta if isinstance(uploaded.meta, dict) else {}
    filename = Path(metadata.get('name') or uploaded.filename).name
    if Path(filename).suffix.lower() not in SUPPORTED_RESOURCE_SUFFIXES:
        return None
    if not uploaded.path:
        raise HTTPException(422, 'Uploaded document has no stored file path')

    def read_source() -> bytes:
        local_path = Storage.get_file(uploaded.path)
        with open(local_path, 'rb') as source:
            return source.read()

    content = await asyncio.to_thread(read_source)
    root = await ensure_personal_folder(db, user_id)
    return await create_document(db, store, user_id, filename, content, folder_id=root.id, source_file_id=file_id)
