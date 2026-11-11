from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import UploadFile
from openpyxl import Workbook
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.models import (
    Document,
    DocumentChunk,
    Folder,
    ResourceIndexJob,
    Revision,
    now,
)
from open_webui.mini_hermes.resource_indexing import (
    MAX_INDEX_ATTEMPTS,
    enqueue_missing_resource_index_jobs,
    enqueue_resource_index_job,
    process_resource_index_job,
)
from open_webui.mini_hermes.service import create_document


def make_docx(text: str) -> bytes:
    from docx import Document as WordDocument

    document = WordDocument()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class MemoryStore:
    def __init__(self):
        self.objects = {}

    async def put(self, key, content, _mime_type):
        self.objects[key] = content

    async def get(self, key):
        return self.objects[key]


@pytest_asyncio.fixture
async def resource_db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[
                    Folder.__table__,
                    Document.__table__,
                    Revision.__table__,
                    DocumentChunk.__table__,
                    ResourceIndexJob.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_queues_indexing_without_parsing_in_request(resource_db):
    store = MemoryStore()
    async with resource_db() as db:
        document = await create_document(db, store, 'alice', 'report.docx', make_docx('queued content'))

        job = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == document.id,
                    ResourceIndexJob.revision == document.current_revision,
                )
            )
        ).scalar_one()
        chunks = (
            await db.execute(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.revision == document.current_revision,
                )
            )
        ).scalar_one()

        assert job.status == 'pending'
        assert job.attempts == 0
        assert chunks == 0


@pytest.mark.asyncio
async def test_xlsx_upload_queues_index_job_when_autoflush_is_disabled():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[Folder.__table__, Document.__table__, Revision.__table__, ResourceIndexJob.__table__],
            )
        )
    sessions = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    workbook = Workbook()
    workbook.active.append(['Program', 'Language'])
    workbook.active.append(['Energy Engineering', 'English'])
    content = BytesIO()
    workbook.save(content)

    try:
        async with sessions() as db:
            document = await create_document(db, MemoryStore(), 'alice', 'M.xlsx', content.getvalue())
            job = (
                await db.execute(
                    select(ResourceIndexJob).where(
                        ResourceIndexJob.document_id == document.id,
                        ResourceIndexJob.revision == 1,
                    )
                )
            ).scalar_one()
            assert job.status == 'pending'
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ('filename', 'content', 'mime_type'),
    [
        ('notes.txt', b'plain text', 'text/plain'),
        ('notes.md', b'# markdown', 'text/markdown'),
        ('table.csv', b'name,value\na,1', 'text/csv'),
        ('table.tsv', b'name\tvalue\na\t1', 'text/tab-separated-values'),
        ('data.json', b'{"value": 1}', 'application/json'),
    ],
)
@pytest.mark.asyncio
async def test_readable_text_resource_formats_are_stored_and_queued(resource_db, filename, content, mime_type):
    store = MemoryStore()
    async with resource_db() as db:
        document = await create_document(db, store, 'alice', filename, content)

        assert document.mime_type == mime_type
        revision = (
            await db.execute(
                select(Revision).where(
                    Revision.document_id == document.id,
                    Revision.number == document.current_revision,
                )
            )
        ).scalar_one()
        assert await store.get(revision.object_key) == content
        job = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == document.id,
                    ResourceIndexJob.revision == document.current_revision,
                )
            )
        ).scalar_one()
        chunks = (
            await db.execute(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.revision == document.current_revision,
                )
            )
        ).scalar_one()

        assert job.status == 'pending'
        assert chunks == 0


@pytest.mark.asyncio
async def test_upload_route_returns_resource_while_indexing_is_queued(resource_db, monkeypatch):
    from open_webui.mini_hermes import routes

    store = MemoryStore()
    monkeypatch.setattr(routes, 'ObjectStore', lambda: store)
    async with resource_db() as db:
        result = await routes.upload_document(
            UploadFile(filename='report.docx', file=BytesIO(make_docx('route content'))),
            SimpleNamespace(id='alice'),
            db,
        )
        job = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == result['id'],
                    ResourceIndexJob.revision == result['revision'],
                )
            )
        ).scalar_one()

        assert result['filename'] == 'report.docx'
        assert job.status == 'pending'


@pytest.mark.asyncio
async def test_indexing_feature_flag_restores_synchronous_rollback(resource_db, monkeypatch):
    monkeypatch.setenv('MH_RESOURCE_INDEX_WORKER_ENABLED', 'false')
    async with resource_db() as db:
        document = await create_document(
            db,
            MemoryStore(),
            'alice',
            'report.docx',
            make_docx('rollback content'),
        )
        chunk_count = (
            await db.execute(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.revision == 1,
                )
            )
        ).scalar_one()
        jobs = (await db.execute(select(ResourceIndexJob))).scalars().all()

        assert chunk_count == 1
        assert jobs == []


@pytest.mark.asyncio
async def test_index_job_is_idempotent_and_indexes_its_exact_revision(resource_db, monkeypatch):
    store = MemoryStore()
    async with resource_db() as db:
        document = await create_document(db, store, 'alice', 'report.docx', make_docx('version one'))
        job = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == document.id,
                    ResourceIndexJob.revision == 1,
                )
            )
        ).scalar_one()

        duplicate = await enqueue_resource_index_job(db, document.id, 1)
        assert duplicate.id == job.id

    from open_webui.mini_hermes import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', resource_db)
    assert await worker.claim_resource_index_job() == job.id

    async with resource_db() as db:
        claimed = await db.get(ResourceIndexJob, job.id)
        assert claimed.status == 'processing'
        assert claimed.attempts == 1
        await process_resource_index_job(db, job.id, store)
        await process_resource_index_job(db, job.id, store)

        chunks = list(
            (
                await db.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == document.id,
                        DocumentChunk.revision == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        stored_job = await db.get(ResourceIndexJob, job.id)

        assert stored_job.status == 'complete'
        assert len(chunks) == 1
        assert 'version one' in chunks[0].content


@pytest.mark.asyncio
async def test_expired_index_job_at_retry_ceiling_becomes_terminal(resource_db, monkeypatch):
    from open_webui.mini_hermes import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', resource_db)
    document = Document(
        id='exhausted-document',
        owner_id='alice',
        filename='report.docx',
        mime_type='application/docx',
        current_revision=1,
    )
    job = ResourceIndexJob(
        id='exhausted-job',
        document_id=document.id,
        revision=1,
        status='processing',
        attempts=MAX_INDEX_ATTEMPTS,
        lease_until=now() - timedelta(minutes=1),
    )
    async with resource_db() as db:
        db.add(document)
        db.add(
            Revision(
                id='exhausted-revision',
                document_id=document.id,
                number=1,
                object_key='exhausted',
                sha256='hash',
                size=1,
            )
        )
        db.add(job)
        await db.commit()

    assert await worker.claim_resource_index_job() is None
    async with resource_db() as db:
        stored = await db.get(ResourceIndexJob, job.id)
        assert stored.status == 'failed'
        assert stored.attempts == MAX_INDEX_ATTEMPTS
        assert stored.lease_until is None


@pytest.mark.asyncio
async def test_legacy_index_backfill_is_bounded_and_idempotent(resource_db):
    async with resource_db() as db:
        db.add(
            Document(
                id='legacy-doc',
                owner_id='alice',
                filename='legacy.docx',
                mime_type='application/docx',
                current_revision=1,
            )
        )
        db.add(
            Revision(
                id='legacy-revision',
                document_id='legacy-doc',
                number=1,
                object_key='legacy',
                sha256='legacy-hash',
                size=10,
            )
        )
        await db.commit()

        assert await enqueue_missing_resource_index_jobs(db, limit=10) == 1
        assert await enqueue_missing_resource_index_jobs(db, limit=10) == 0
        jobs = list((await db.execute(select(ResourceIndexJob))).scalars().all())

        assert len(jobs) == 1
        assert jobs[0].status == 'pending'
