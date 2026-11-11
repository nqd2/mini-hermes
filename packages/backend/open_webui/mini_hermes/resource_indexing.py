"""Idempotent worker operations for versioned document indexing."""

from __future__ import annotations

import datetime as dt
import logging
import os
from uuid import uuid4

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import replace_document_chunks
from .models import Document, DocumentChunk, ResourceIndexJob, Revision, now
from .revision_integrity import get_verified_revision_content

log = logging.getLogger(__name__)
MAX_INDEX_ATTEMPTS = 5
INDEX_LEASE = dt.timedelta(minutes=5)


async def enqueue_resource_index_job(
    db: AsyncSession,
    document_id: str,
    revision: int,
) -> ResourceIndexJob:
    """Queue one job per immutable revision; concurrent enqueue is a no-op."""
    values = {
        'id': str(uuid4()),
        'document_id': document_id,
        'revision': revision,
        'status': 'pending',
        'attempts': 0,
        'next_attempt_at': now(),
        'created_at': now(),
        'updated_at': now(),
    }
    dialect = db.bind.dialect.name
    if dialect == 'postgresql':
        statement = postgres_insert(ResourceIndexJob).values(**values)
        await db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[ResourceIndexJob.document_id, ResourceIndexJob.revision],
            )
        )
    elif dialect == 'sqlite':
        statement = sqlite_insert(ResourceIndexJob).values(**values)
        await db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[ResourceIndexJob.document_id, ResourceIndexJob.revision],
            )
        )
    else:
        existing = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == document_id,
                    ResourceIndexJob.revision == revision,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(ResourceIndexJob(**values))
            await db.flush()

    return (
        await db.execute(
            select(ResourceIndexJob).where(
                ResourceIndexJob.document_id == document_id,
                ResourceIndexJob.revision == revision,
            )
        )
    ).scalar_one()


def resource_index_worker_enabled() -> bool:
    return os.getenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'true').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }


async def enqueue_missing_resource_index_jobs(db: AsyncSession, limit: int = 100) -> int:
    """Queue a bounded, repeatable backfill for legacy revisions without chunks."""
    if not resource_index_worker_enabled():
        return 0
    candidates = (
        await db.execute(
            select(Revision.document_id, Revision.number)
            .where(
                ~exists(
                    select(1).where(
                        ResourceIndexJob.document_id == Revision.document_id,
                        ResourceIndexJob.revision == Revision.number,
                    )
                ),
                ~exists(
                    select(1).where(
                        DocumentChunk.document_id == Revision.document_id,
                        DocumentChunk.revision == Revision.number,
                    )
                ),
            )
            .order_by(Revision.document_id, Revision.number)
            .limit(max(1, limit))
        )
    ).all()
    for document_id, revision in candidates:
        await enqueue_resource_index_job(db, document_id, revision)
    if candidates:
        await db.commit()
    return len(candidates)


async def process_resource_index_job(
    db: AsyncSession,
    job_id: str,
    store,
) -> bool:
    """Index one exact revision under a row lock, making duplicate execution safe."""
    job = (
        await db.execute(
            select(ResourceIndexJob)
            .where(
                ResourceIndexJob.id == job_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return False
    if job.status == 'complete':
        return True

    if job.status != 'processing':
        job.status = 'processing'
        job.attempts += 1
    job.lease_until = now() + INDEX_LEASE
    await db.flush()

    try:
        document = await db.get(Document, job.document_id)
        revision = (
            await db.execute(
                select(Revision).where(
                    Revision.document_id == job.document_id,
                    Revision.number == job.revision,
                )
            )
        ).scalar_one_or_none()
        if document is None or revision is None:
            raise LookupError('Indexed document revision no longer exists')
        content = await get_verified_revision_content(store, revision)
        await replace_document_chunks(db, document.id, revision.number, document.filename, content)
        job.status = 'complete'
        job.error = None
        job.lease_until = None
        await db.commit()
        return True
    except Exception as exc:
        await db.rollback()
        job = (
            await db.execute(
                select(ResourceIndexJob)
                .where(
                    ResourceIndexJob.id == job_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.status = 'failed'
        job.error = str(exc)[:1000]
        job.lease_until = None
        job.next_attempt_at = now() + dt.timedelta(seconds=min(3600, 2 ** min(job.attempts, 10)))
        if isinstance(exc, LookupError):
            job.attempts = MAX_INDEX_ATTEMPTS
        await db.commit()
        log.exception('Resource indexing failed for job %s', job_id)
        return False
