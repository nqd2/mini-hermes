"""Agent-visible read and proposal tools. Commit and Drive writes are excluded."""

from sqlalchemy import select

from open_webui.internal.db import get_async_db

from .edits import extract_text
from .models import Document, Preview
from .service import create_preview, owned_document, revision_for
from .storage import ObjectStore


async def list_editable_documents(__user__: dict = None) -> list[dict]:
    """List the current user's documents available for agent reading and edit proposals."""
    if not __user__:
        return []
    async with get_async_db() as db:
        docs = (await db.execute(select(Document).where(Document.owner_id == __user__['id']))).scalars().all()
        return [{'id': doc.id, 'filename': doc.filename, 'revision': doc.current_revision} for doc in docs]


async def read_editable_document(document_id: str, __user__: dict = None) -> str:
    """Read text from one of the current user's Office or PDF documents by document ID."""
    if not __user__:
        return 'User context unavailable'
    async with get_async_db() as db:
        doc = await owned_document(db, document_id, __user__['id'])
        revision = await revision_for(db, doc.id, doc.current_revision)
        content = await ObjectStore().get(revision.object_key)
        return extract_text(content, doc.filename)[:50_000]


async def propose_document_edit(document_id: str, operations: list[dict], __user__: dict = None) -> dict:
    """Propose Office document edits for preview. The user must confirm the preview to commit and sync Drive."""
    if not __user__:
        return {'error': 'User context unavailable'}
    async with get_async_db() as db:
        doc = await owned_document(db, document_id, __user__['id'])
        preview = await create_preview(db, doc, __user__['id'], operations)
        return {'preview_id': preview.id, 'status': preview.status, 'document_id': doc.id}
