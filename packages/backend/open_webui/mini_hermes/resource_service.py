"""Resource API operations shared by backend retrieval and agent-tool adapters."""

from sqlalchemy import func, select

from open_webui.internal.db import get_async_db
from open_webui.utils.chat_id import is_saved_chat_id

from .chunks import replace_document_chunks, search_document_chunks
from .execution.contracts import ResourceIndexFailed, ResourceIndexPending
from .library import (
    chat_attachment_file_ids,
    list_chat_documents,
    set_chat_document_access,
)
from .models import DocumentChunk, ResourceIndexJob, Revision, now
from .resource_indexing import (
    MAX_INDEX_ATTEMPTS,
    enqueue_resource_index_job,
    resource_index_worker_enabled,
)
from .resource_uploads import register_chat_upload
from .service import get_verified_revision_content
from .storage import ObjectStore


async def list_chat_resource_manifest(chat_id: str | None, user_id: str) -> list[dict]:
    """Return metadata-only references for resources attached to a saved chat."""
    if not is_saved_chat_id(chat_id):
        return []
    async with get_async_db() as db:
        documents = await list_chat_documents(db, chat_id, user_id)
        return [
            {
                'id': str(document.source_file_id or document.id),
                'resource_id': document.id,
                'type': 'file',
                'name': document.filename,
                'filename': document.filename,
                'revision': document.current_revision,
            }
            for document in documents
        ]


async def ensure_revision_index(db, store, document, revision) -> tuple[int, str | None, str | None]:
    count = (await db.execute(select(func.count()).select_from(DocumentChunk).where(
        DocumentChunk.document_id == document.id,
        DocumentChunk.revision == revision.number,
    ))).scalar_one()
    if count:
        return int(count), None, None

    index_job = (await db.execute(select(ResourceIndexJob).where(
        ResourceIndexJob.document_id == document.id,
        ResourceIndexJob.revision == revision.number,
    ))).scalar_one_or_none()
    if not resource_index_worker_enabled():
        if index_job is None:
            index_job = await enqueue_resource_index_job(db, document.id, revision.number)
        if index_job.status == 'complete':
            return 0, None, None
        content = await get_verified_revision_content(store, revision)
        await replace_document_chunks(db, document.id, revision.number, document.filename, content)
        index_job.status = 'complete'
        index_job.error = None
        index_job.lease_until = None
        await db.commit()
        count = (await db.execute(select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.revision == revision.number,
        ))).scalar_one()
        return int(count), None, None

    if index_job is None:
        index_job = await enqueue_resource_index_job(db, document.id, revision.number)
        await db.commit()
    if index_job.status == 'complete':
        return 0, None, None
    if index_job.status == 'failed' and index_job.attempts >= MAX_INDEX_ATTEMPTS:
        return 0, None, index_job.id
    if (
        index_job.status == 'processing'
        and index_job.attempts >= MAX_INDEX_ATTEMPTS
        and (index_job.lease_until is None or index_job.lease_until <= now())
    ):
        index_job.status = 'failed'
        index_job.error = 'Indexing lease expired after the retry limit.'
        index_job.lease_until = None
        await db.commit()
        return 0, None, index_job.id
    return 0, index_job.id, None


_ensure_revision_index = ensure_revision_index


async def search_chat_files(
    user_id: str, files: list[dict] | None, chat_id: str | None, query: str,
    *, max_chunks: int = 12, per_document: int = 3, full_context: bool = False,
    max_total_chars: int = 24_000, priority_file_ids: list[str] | None = None,
    bounded_initial_read: bool = False,
    scope_file_ids: list[str] | None = None,
) -> list[dict]:
    """Search only files in the current attachment/chat ACL and return provenance."""
    requested_ids = {
        str(item['id']) for item in files or []
        if isinstance(item, dict) and item.get('id') and item.get('type', 'file') == 'file'
    }
    scope_ids = {str(file_id) for file_id in scope_file_ids or []}
    if scope_ids:
        requested_ids.intersection_update(scope_ids)
    async with get_async_db() as db:
        if is_saved_chat_id(chat_id):
            requested_ids.update(await chat_attachment_file_ids(db, chat_id, user_id))
        if scope_ids:
            requested_ids.intersection_update(scope_ids)
        store = ObjectStore()
        documents = {}
        for file_id in requested_ids:
            doc = await register_chat_upload(db, user_id, file_id, store)
            if doc:
                documents[doc.id] = doc
                if is_saved_chat_id(chat_id):
                    await set_chat_document_access(db, chat_id, user_id, doc.id, True)
        if is_saved_chat_id(chat_id):
            scoped_chat_documents = await list_chat_documents(db, chat_id, user_id)
            if scope_ids:
                scoped_chat_documents = [
                    doc for doc in scoped_chat_documents
                    if str(doc.source_file_id or doc.id) in scope_ids or doc.id in scope_ids
                ]
            documents.update({doc.id: doc for doc in scoped_chat_documents})

        indexed_documents = []
        pending_index_jobs = []
        failed_index_jobs = []
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
            count, pending_job_id, failed_job_id = await ensure_revision_index(
                db, store, doc, revision,
            )
            if failed_job_id:
                failed_index_jobs.append(failed_job_id)
                continue
            if pending_job_id:
                pending_index_jobs.append(pending_job_id)
                continue
            if not count:
                continue
            indexed_documents.append((doc, revision))

        if failed_index_jobs:
            raise ResourceIndexFailed(failed_index_jobs)
        if pending_index_jobs:
            raise ResourceIndexPending(pending_index_jobs)

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
            retrieval_mode = 'focused_search'
            if not matches and bounded_initial_read:
                first_by_section = (
                    select(
                        DocumentChunk.section.label('section'),
                        func.min(DocumentChunk.ordinal).label('first_ordinal'),
                    )
                    .where(
                        DocumentChunk.document_id == doc.id,
                        DocumentChunk.revision == revision.number,
                    )
                    .group_by(DocumentChunk.section)
                    .order_by(func.min(DocumentChunk.ordinal))
                    .limit(per_document)
                    .subquery()
                )
                matches = list((await db.execute(
                    select(DocumentChunk).join(
                        first_by_section,
                        DocumentChunk.ordinal == first_by_section.c.first_ordinal,
                    ).where(
                        DocumentChunk.document_id == doc.id,
                        DocumentChunk.revision == revision.number,
                    ).order_by(DocumentChunk.ordinal)
                )).scalars().all())
                retrieval_mode = 'bounded_initial_read'
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
                        'retrieval_mode': retrieval_mode,
                        **(chunk.metadata_json or {}),
                    }],
                })
                if len(results) >= max_chunks:
                    return results
        return results
