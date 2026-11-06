"""Resource API operations shared by backend retrieval and agent-tool adapters."""

from sqlalchemy import func, select

from open_webui.internal.db import get_async_db
from open_webui.utils.chat_id import is_saved_chat_id

from .chunks import replace_document_chunks, search_document_chunks
from .library import (
    chat_attachment_file_ids,
    list_chat_documents,
    register_chat_upload,
    set_chat_document_access,
)
from .models import DocumentChunk, Revision
from .storage import ObjectStore


async def search_chat_files(
    user_id: str, files: list[dict] | None, chat_id: str | None, query: str,
    *, max_chunks: int = 12, per_document: int = 3, full_context: bool = False,
    max_total_chars: int = 24_000, priority_file_ids: list[str] | None = None,
) -> list[dict]:
    """Search only files in the current attachment/chat ACL and return provenance."""
    requested_ids = {
        str(item['id']) for item in files or []
        if isinstance(item, dict) and item.get('id') and item.get('type', 'file') == 'file'
    }
    async with get_async_db() as db:
        if is_saved_chat_id(chat_id):
            requested_ids.update(await chat_attachment_file_ids(db, chat_id, user_id))
        store = ObjectStore()
        documents = {}
        for file_id in requested_ids:
            doc = await register_chat_upload(db, user_id, file_id, store)
            if doc:
                documents[doc.id] = doc
                if is_saved_chat_id(chat_id):
                    await set_chat_document_access(db, chat_id, user_id, doc.id, True)
        if is_saved_chat_id(chat_id):
            documents.update({doc.id: doc for doc in await list_chat_documents(db, chat_id, user_id)})

        indexed_documents = []
        preferred = {str(file_id) for file_id in priority_file_ids or []}
        for doc in sorted(
            documents.values(),
            key=lambda item: (str(item.source_file_id or item.id) not in preferred, item.filename.casefold()),
        ):
            revision = (await db.execute(select(Revision).where(
                Revision.document_id == doc.id, Revision.number == doc.current_revision
            ))).scalar_one_or_none()
            if not revision:
                continue
            count = (await db.execute(select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == doc.id, DocumentChunk.revision == revision.number
            ))).scalar_one()
            if not count:
                content = await store.get(revision.object_key)
                await replace_document_chunks(db, doc.id, revision.number, doc.filename, content)
                await db.commit()
            indexed_documents.append((doc, revision))

        if full_context and indexed_documents:
            full_sources = []
            total_chars = 0
            chunk_counts = {}
            for doc, revision in indexed_documents:
                summary = (await db.execute(select(
                    func.coalesce(func.sum(func.length(DocumentChunk.content)), 0),
                    func.count(),
                ).where(
                    DocumentChunk.document_id == doc.id,
                    DocumentChunk.revision == revision.number,
                ))).one()
                total_chars += int(summary[0])
                chunk_counts[doc.id] = int(summary[1])
            complete = (
                all(chunk_counts.get(doc.id, 0) > 0 for doc, _revision in indexed_documents)
                and total_chars <= max_total_chars
            )
            if complete:
                for doc, revision in indexed_documents:
                    chunks = list((await db.execute(select(DocumentChunk).where(
                        DocumentChunk.document_id == doc.id,
                        DocumentChunk.revision == revision.number,
                    ).order_by(DocumentChunk.ordinal))).scalars().all())
                    section_lengths = {}
                    for chunk in chunks:
                        section_lengths[chunk.section] = max(
                            section_lengths.get(chunk.section, 0), chunk.end_offset
                        )
                    source_id = doc.source_file_id or doc.id
                    full_sources.extend({
                        'source': {'id': source_id, 'name': doc.filename, 'type': 'file'},
                        'document': [chunk.content],
                        'metadata': [{
                            'file_id': source_id, 'name': doc.filename, 'source': doc.filename,
                            'document_id': doc.id, 'revision': revision.number,
                            'evidence_id': chunk.id, 'section': chunk.section, 'page': chunk.page,
                            'start_offset': chunk.start_offset, 'end_offset': chunk.end_offset,
                            'total_chars': section_lengths[chunk.section], 'coverage_complete': True,
                            **(chunk.metadata_json or {}),
                        }],
                    } for chunk in chunks)
            if complete:
                return full_sources

        results = []
        for doc, revision in indexed_documents:
            matches = await search_document_chunks(db, doc.id, revision.number, query, per_document)
            if not matches:
                matches = list((await db.execute(select(DocumentChunk).where(
                    DocumentChunk.document_id == doc.id, DocumentChunk.revision == revision.number
                ).order_by(DocumentChunk.ordinal).limit(1))).scalars().all())
            source_id = doc.source_file_id or doc.id
            for chunk in matches:
                results.append({
                    'source': {'id': source_id, 'name': doc.filename, 'type': 'file'},
                    'document': [chunk.content],
                    'metadata': [{
                        'file_id': source_id, 'name': doc.filename, 'source': doc.filename,
                        'document_id': doc.id, 'revision': revision.number,
                        'evidence_id': chunk.id, 'section': chunk.section, 'page': chunk.page,
                        'start_offset': chunk.start_offset, 'end_offset': chunk.end_offset,
                        'total_chars': chunk.end_offset, 'coverage_complete': False,
                        **(chunk.metadata_json or {}),
                    }],
                })
                if len(results) >= max_chunks:
                    return results
        return results
