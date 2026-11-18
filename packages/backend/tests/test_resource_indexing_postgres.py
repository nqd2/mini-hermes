"""PostgreSQL integration coverage for resource index/task locking and races.

Set ``AGENT_RUNTIME_TEST_POSTGRES_URL`` to an isolated, disposable PostgreSQL database URL
to enable these tests. Each test creates and drops its own random schema.
"""

import asyncio
import datetime as dt
import hashlib
import os
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.documents.models import (
    ChatResource,
    Document,
    DocumentChunk,
    Folder,
    Preview,
    ResourceIndexJob,
    ResourceAnalysis,
    ResourceAnalysisEvidence,
    ResourceAnalysisTrace,
    ResourceTask,
    Revision,
    RevisionApproval,
    RevisionOutbox,
    SyncJob,
    now,
)
from weai.documents.resource_indexing import enqueue_missing_resource_index_jobs
from weai.documents.revision_service import compute_patch_hash
from weai.models.users import User


@pytest.fixture(scope='module')
def event_loop_policy():
    """psycopg async requires a selector loop on Windows, independent of DATABASE_URL."""
    if sys.platform == 'win32':
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def postgres_resource_db():
    database_url = os.getenv('AGENT_RUNTIME_TEST_POSTGRES_URL')
    if not database_url:
        pytest.skip('Set AGENT_RUNTIME_TEST_POSTGRES_URL to run PostgreSQL integration tests')

    url = make_url(database_url)
    if url.drivername == 'postgresql':
        url = url.set(drivername='postgresql+psycopg')
    schema = f'ar_test_{uuid4().hex}'
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    test_engine = None
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        isolated_url = url.set(query={**dict(url.query), 'options': f'-csearch_path={schema}'})
        test_engine = create_async_engine(isolated_url, pool_pre_ping=True)
        async with test_engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Document.metadata.create_all(
                    sync,
                    tables=[
                        Folder.__table__,
                        ChatResource.__table__,
                        Document.__table__,
                        Revision.__table__,
                        DocumentChunk.__table__,
                        ResourceIndexJob.__table__,
                        ResourceTask.__table__,
                        ResourceAnalysis.__table__,
                        ResourceAnalysisEvidence.__table__,
                        ResourceAnalysisTrace.__table__,
                        Preview.__table__,
                        RevisionApproval.__table__,
                        RevisionOutbox.__table__,
                        SyncJob.__table__,
                    ],
                )
            )
            await connection.run_sync(lambda sync: User.__table__.create(sync, checkfirst=True))

        seed_sessions = async_sessionmaker(test_engine, expire_on_commit=False)
        async with seed_sessions() as seed:
            seed.add_all([
                User(id=user_id, email=f'{user_id}@example.invalid', name=user_id,
                     role='user', created_at=1, updated_at=1, last_active_at=1)
                for user_id in ('owner-1', 'collaborator', 'postgres-test')
            ])
            await seed.commit()

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [None, 'cancel', 'cancel_survey', 'revision', 'revoke', 'newer_message', 'during_publish', 'ocr', 'ocr_failed', 'lease'])
async def test_chat_analysis_atomic_publication_and_scope(postgres_resource_db, monkeypatch, change):
    from io import BytesIO
    from types import SimpleNamespace
    from openpyxl import Workbook
    from weai.internal import db as internal_db
    from weai.documents import document_analysis_tasks as analysis, worker
    from weai.documents.document_analysis_trace import read_analysis_trace
    from weai.documents.resource_tasks import request_task_cancel
    from weai.models.chats import Chat
    from weai.models.chat_messages import ChatMessage

    sessions = postgres_resource_db
    async with sessions() as db:
        conn = await db.connection()
        await conn.run_sync(lambda sync: Chat.__table__.create(sync, checkfirst=True))
        await conn.run_sync(lambda sync: ChatMessage.__table__.create(sync, checkfirst=True))
        db.add(Document(
            id='analysis-doc', owner_id='owner-1', filename='scan.pdf' if change in {'ocr', 'ocr_failed'} else 'sample.xlsx',
            mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', current_revision=1,
        ))
        await db.flush()
        db.add(ChatResource(id='grant', chat_id='analysis-chat', document_id='analysis-doc', granted_by='owner-1'))
        db.add(Chat(id='analysis-chat', user_id='owner-1', title='Test', created_at=1, updated_at=1, chat={
            'history': {'messages': {'response': {
                'id': 'response', 'role': 'assistant', 'user_id': 'owner-1', 'content': '', 'timestamp': 1,
            }}}
        }))
        await db.commit()

    @asynccontextmanager
    async def test_db():
        async with sessions() as session:
            yield session

    workbook = Workbook()
    workbook.active.append(['Programs', 'Program Code'])
    workbook.active.append(['Engineering', '001234'])
    buffer = BytesIO()
    if change in {'ocr', 'ocr_failed'}:
        from PIL import Image

        Image.new('RGB', (64, 64), 'white').save(buffer, format='PDF')
    else:
        workbook.save(buffer)

    async def source(*args):
        return buffer.getvalue()

    lease_expired = False

    async def provider(messages, **kwargs):
        nonlocal lease_expired
        if change == 'lease' and not lease_expired:
            lease_expired = True
            async with sessions() as db:
                (await db.get(ResourceTask, task_id)).lease_until = now() - dt.timedelta(seconds=1)
                await db.commit()
        if change == 'during_publish' and 'Combine these evidence' in messages[0]['content']:
            async with sessions() as db:
                (await db.get(Document, 'analysis-doc')).current_revision = 2
                await db.commit()
        return '- Kỹ thuật [E1]'

    async def target(*args):
        return {'index': 0, 'url': 'https://provider.example', 'model': 'test'}

    monkeypatch.setattr(internal_db, 'get_async_db', test_db)
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(analysis, '_source', source)
    monkeypatch.setattr(analysis, 'call_provider', provider)
    monkeypatch.setattr(analysis, '_provider_target', target)
    if change == 'ocr':
        monkeypatch.setattr(analysis, 'ocr_unit', lambda raw, unit: ('Scanned text', [0.97]))
    elif change == 'ocr_failed':
        def fail_ocr(raw, unit):
            raise ValueError('secret=never-publish')
        monkeypatch.setattr(analysis, 'ocr_unit', fail_ocr)
    elif change == 'cancel_survey':
        async def cancel_and_flush(db, trace, raw, filename):
            trace.emit('stdout', 'Starting workbook scan.\n')
            async with sessions() as other:
                await request_task_cancel(other, task_id, 'owner-1')
                await other.commit()
            await trace.flush(db)
            return await real_survey(db, trace, raw, filename)

        real_survey = analysis._survey_with_trace
        monkeypatch.setattr(analysis, '_survey_with_trace', cancel_and_flush)
    user = SimpleNamespace(id='owner-1', role='user')
    metadata = {'chat_id': 'analysis-chat', 'message_id': 'response', 'resource_aware_files': True}
    first = await analysis.queue_chat_analysis(metadata, user, 'Tóm tắt file đi')
    repeated = await analysis.queue_chat_analysis(metadata, user, 'Tóm tắt file đi')
    task_id = first['analysis']['task_id']
    assert repeated['analysis']['task_id'] == task_id
    assert await worker.claim_resource_task() == task_id
    await worker.process_resource_task_job(task_id)

    # Every subsequent step uses a new session, including after our checkpoint.
    async with sessions() as db:
        saved = await db.get(ResourceTask, task_id)
        if change == 'cancel_survey':
            message = await db.get(ChatMessage, 'analysis-chat-response')
            trace = await read_analysis_trace(db, task_id, 0)
            assert saved.status == 'cancelled'
            assert message.meta['document_analysis']['status'] == 'cancelled'
            assert trace['events'][-1]['data']['status'] == 'cancelled'
            return
        assert saved.phase == ('ocr' if change in {'ocr', 'ocr_failed'} else 'summarize')
        trace = await read_analysis_trace(db, task_id, 0, limit=1)
        assert trace['has_more']
        start = trace['events'][0]
        assert start['kind'] == 'started'
        assert 'def survey_document(' in start['data']['code']
        followup = await read_analysis_trace(db, task_id, trace['next_after'])
        assert all(event['sequence'] > trace['next_after'] for event in followup['events'])
        assert any(event['kind'] == 'stdout' for event in followup['events'])
        if change == 'cancel':
            await request_task_cancel(db, task_id, user.id)
        elif change == 'revision':
            (await db.get(Document, 'analysis-doc')).current_revision = 2
        elif change == 'revoke':
            await db.delete(await db.get(ChatResource, 'grant'))
        elif change == 'newer_message':
            from weai.models.chats import Chats

            async with Chats.edit_message_output('analysis-chat', 'response', db=db) as message:
                message['meta']['document_analysis']['task_id'] = 'newer-run'
                message['content'] = 'Newer answer'
        await db.commit()
    if change == 'newer_message':
        stale = await analysis.queue_chat_analysis(metadata, user, 'Tóm tắt file đi')
        assert stale == {'skip_event': True}

    for _ in range(9):
        claimed = await worker.claim_resource_task()
        if not claimed:
            break
        assert claimed == task_id
        await worker.process_resource_task_job(claimed)

    async with sessions() as db:
        task = await db.get(ResourceTask, task_id)
        message = await db.get(ChatMessage, 'analysis-chat-response')
        if change in {None, 'newer_message', 'ocr', 'lease'}:
            assert task.status == 'completed'
            assert 'Kỹ thuật [E1]' in task.result
            first_page = await analysis.paginate_analysis(db, task_id, 0, 1)
            unit = first_page['evidence'][0]
            if change == 'ocr':
                assert unit['text'] == 'Scanned text' and unit['confidence'] == [0.97]
            else:
                assert '001234' in unit['text']
            assert message.content == ('Newer answer' if change == 'newer_message' else task.result)
        else:
            assert task.status == ('cancelled' if change == 'cancel' else 'failed')
            assert not message.content
        if change == 'ocr_failed':
            trace = await read_analysis_trace(db, task_id, 0)
            assert any(event['kind'] == 'stderr' and 'ValueError' in event['data']['text'] for event in trace['events'])
            assert 'never-publish' not in str(trace)
        if change == 'lease':
            trace = await read_analysis_trace(db, task_id, 0)
            interrupted = next(event for event in trace['events'] if event['data'].get('status') == 'interrupted')
            assert not any(event['kind'] == 'stdout' and event['step_id'] == interrupted['step_id']
                           for event in trace['events'])


async def _seed_pending_jobs(session_factory, count: int = 2) -> list[str]:
    created_at = now()
    ids = [f'pg-index-job-{index}' for index in range(count)]
    async with session_factory() as db:
        for index, job_id in enumerate(ids):
            document_id = f'pg-index-document-{index}'
            db.add(
                Document(
                    id=document_id,
                    owner_id='postgres-test',
                    filename=f'report-{index}.txt',
                    mime_type='text/plain',
                    current_revision=1,
                )
            )
            db.add(
                ResourceIndexJob(
                    id=job_id,
                    document_id=document_id,
                    revision=1,
                    status='pending',
                    attempts=0,
                    next_attempt_at=created_at - dt.timedelta(seconds=1),
                    created_at=created_at + dt.timedelta(seconds=index),
                    updated_at=created_at,
                )
            )
        await db.commit()
    return ids


async def _seed_pending_tasks(session_factory, count: int = 2) -> list[str]:
    created_at = now()
    ids = [f'pg-resource-task-{index}' for index in range(count)]
    async with session_factory() as db:
        for index, task_id in enumerate(ids):
            document_id = f'pg-task-document-{index}'
            db.add(
                Document(
                    id=document_id,
                    owner_id='postgres-test',
                    filename=f'task-{index}.txt',
                    mime_type='text/plain',
                    current_revision=1,
                )
            )
            db.add(
                ResourceTask(
                    id=task_id,
                    document_id=document_id,
                    owner_id='postgres-test',
                    chat_id=f'postgres-chat-{index}',
                    revision=1,
                    created_at=created_at + dt.timedelta(seconds=index),
                )
            )
        await db.commit()
    return ids


@pytest.mark.asyncio
async def test_index_worker_skips_a_locked_job(postgres_resource_db, monkeypatch):
    from weai.documents import worker

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    first_id, second_id = await _seed_pending_jobs(postgres_resource_db)

    async with postgres_resource_db() as lock_session:
        async with lock_session.begin():
            locked = (
                await lock_session.execute(
                    select(ResourceIndexJob).where(ResourceIndexJob.id == first_id).with_for_update()
                )
            ).scalar_one()
            assert locked.status == 'pending'

            claimed_id = await worker.claim_resource_index_job()

            assert claimed_id == second_id

    async with postgres_resource_db() as db:
        first = await db.get(ResourceIndexJob, first_id)
        second = await db.get(ResourceIndexJob, second_id)
        assert first.status == 'pending'
        assert second.status == 'processing'
        assert second.attempts == 1


@pytest.mark.asyncio
async def test_parallel_index_workers_claim_distinct_jobs(postgres_resource_db, monkeypatch):
    from weai.documents import worker

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    expected_ids = set(await _seed_pending_jobs(postgres_resource_db))

    claimed_ids = await asyncio.gather(
        worker.claim_resource_index_job(),
        worker.claim_resource_index_job(),
    )

    assert set(claimed_ids) == expected_ids
    async with postgres_resource_db() as db:
        claimed = (
            (await db.execute(select(ResourceIndexJob).where(ResourceIndexJob.id.in_(claimed_ids)))).scalars().all()
        )
        assert len(claimed) == 2
        assert all(job.status == 'processing' and job.attempts == 1 for job in claimed)


@pytest.mark.asyncio
async def test_index_worker_reclaims_an_expired_lease(postgres_resource_db, monkeypatch):
    from weai.documents import worker

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    (job_id,) = await _seed_pending_jobs(postgres_resource_db, count=1)
    async with postgres_resource_db() as db:
        job = await db.get(ResourceIndexJob, job_id)
        job.status = 'processing'
        job.attempts = 1
        job.lease_until = now() - dt.timedelta(minutes=1)
        await db.commit()

    assert await worker.claim_resource_index_job() == job_id
    async with postgres_resource_db() as db:
        job = await db.get(ResourceIndexJob, job_id)
        lease_until = job.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=dt.UTC)
        assert job.status == 'processing'
        assert job.attempts == 2
        assert lease_until > now()


@pytest.mark.asyncio
async def test_parallel_legacy_backfills_preserve_one_job_per_revision(postgres_resource_db):
    document_id = 'pg-legacy-document'
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='legacy.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            Revision(
                id='pg-legacy-revision',
                document_id=document_id,
                number=1,
                object_key='legacy-object',
                sha256='legacy-hash',
                size=10,
            )
        )
        await db.commit()

    async def backfill_once():
        async with postgres_resource_db() as db:
            return await enqueue_missing_resource_index_jobs(db, limit=10)

    await asyncio.gather(backfill_once(), backfill_once())

    async with postgres_resource_db() as db:
        jobs = (
            (
                await db.execute(
                    select(ResourceIndexJob).where(
                        ResourceIndexJob.document_id == document_id,
                        ResourceIndexJob.revision == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == 'pending'


@pytest.mark.asyncio
async def test_resource_task_worker_skips_a_locked_task(postgres_resource_db, monkeypatch):
    from weai.documents import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    first_id, second_id = await _seed_pending_tasks(postgres_resource_db)

    async with postgres_resource_db() as lock_session:
        async with lock_session.begin():
            locked = (
                await lock_session.execute(select(ResourceTask).where(ResourceTask.id == first_id).with_for_update())
            ).scalar_one()
            assert locked.status == 'pending'

            claimed_id = await worker.claim_resource_task()

            assert claimed_id == second_id

    async with postgres_resource_db() as db:
        first = await db.get(ResourceTask, first_id)
        second = await db.get(ResourceTask, second_id)
        assert first.status == 'pending'
        assert second.status == 'processing'
        assert second.lease_until is not None


@pytest.mark.asyncio
async def test_resource_task_worker_reclaims_an_expired_lease(postgres_resource_db, monkeypatch):
    from weai.documents import worker

    monkeypatch.setattr(worker, 'AsyncSessionLocal', postgres_resource_db)
    (task_id,) = await _seed_pending_tasks(postgres_resource_db, count=1)
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        task.status = 'processing'
        task.lease_until = now() - dt.timedelta(minutes=1)
        await db.commit()

    assert await worker.claim_resource_task() == task_id
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        lease_until = task.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=dt.UTC)
        assert task.status == 'processing'
        assert lease_until > now()


@pytest.mark.asyncio
async def test_resource_task_cancel_and_resume_commit_in_postgres(postgres_resource_db):
    from weai.documents.resource_tasks import request_task_cancel, resume_summary_task

    document_id = 'pg-cancel-resume-document'
    task_id = 'pg-cancel-resume-task'
    async with postgres_resource_db() as db:
        document = Document(
            id=document_id,
            owner_id='postgres-test',
            filename='cancel-resume.txt',
            mime_type='text/plain',
            current_revision=1,
        )
        db.add(document)
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                revision=1,
                status='pending',
            )
        )
        await db.commit()

        cancelled = await request_task_cancel(db, task_id, 'postgres-test')
        assert cancelled.status == 'cancelled'
        assert cancelled.cancel_requested is True

    async with postgres_resource_db() as db:
        cancelled = await db.get(ResourceTask, task_id)
        document = await db.get(Document, document_id)
        assert cancelled.status == 'cancelled'
        assert cancelled.cancel_requested is True

        resumed = await resume_summary_task(db, task_id, 'postgres-test', document)
        assert resumed.status == 'pending'
        assert resumed.cancel_requested is False


@pytest.mark.asyncio
async def test_resource_task_checkpoint_and_revision_resume_validation_in_postgres(postgres_resource_db):
    from fastapi import HTTPException

    from weai.documents.resource_tasks import request_task_cancel, resume_summary_task

    document_id = 'pg-resume-checkpoint-document'
    task_id = 'pg-resume-checkpoint-task'
    checkpoint = {
        'items': [{'summary': 'saved section', 'evidence_ids': ['stable-chunk-id']}],
        'reduced': [],
    }
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='checkpoint.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                revision=1,
                status='pending',
                checkpoint=checkpoint,
                next_ordinal=7,
                completed_units=7,
            )
        )
        await db.commit()
        await request_task_cancel(db, task_id, 'postgres-test')

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        resumed = await resume_summary_task(db, task_id, 'postgres-test', document)
        assert resumed.status == 'pending'
        assert resumed.next_ordinal == 7
        assert resumed.completed_units == 7
        assert resumed.checkpoint == checkpoint

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        await request_task_cancel(db, task_id, 'postgres-test')
        document.current_revision = 2
        await db.commit()

        with pytest.raises(HTTPException) as error:
            await resume_summary_task(db, task_id, 'postgres-test', document)
        assert error.value.status_code == 409
        assert 'revision changed' in error.value.detail.lower()


@pytest.mark.asyncio
async def test_resource_task_resume_rechecks_revoked_chat_acl_in_postgres(postgres_resource_db, monkeypatch):
    from fastapi import HTTPException

    from weai.agent_runtime import tools
    from weai.documents.library import set_chat_document_access

    document_id = 'pg-acl-resume-document'
    task_id = 'pg-acl-resume-task'
    chat_id = 'pg-acl-resume-chat'
    async with postgres_resource_db() as db:
        document = Document(
            id=document_id,
            owner_id='postgres-test',
            filename='acl.txt',
            mime_type='text/plain',
            current_revision=1,
        )
        db.add(document)
        db.add(
            ResourceTask(
                id=task_id,
                document_id=document_id,
                owner_id='postgres-test',
                chat_id=chat_id,
                revision=1,
                status='cancelled',
                checkpoint={'items': [], 'reduced': []},
            )
        )
        await db.commit()
        await set_chat_document_access(db, chat_id, 'postgres-test', document_id, True)
        await set_chat_document_access(db, chat_id, 'postgres-test', document_id, False)

    @asynccontextmanager
    async def open_db():
        async with postgres_resource_db() as db:
            yield db

    monkeypatch.setattr(tools, 'get_async_db', open_db)

    with pytest.raises(HTTPException) as error:
        await tools.resume_document_task(
            task_id,
            __user__={'id': 'postgres-test', 'role': 'user'},
            __files__=[],
            __chat_id__=chat_id,
        )
    assert error.value.status_code == 404
    async with postgres_resource_db() as db:
        task = await db.get(ResourceTask, task_id)
        assert task.status == 'cancelled'


@pytest.mark.asyncio
async def test_preview_commit_publishes_only_verified_staged_artifacts_in_postgres(postgres_resource_db, monkeypatch):
    from weai.documents.service import commit_preview

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    document_id = 'pg-staged-commit-document'
    preview_id = 'pg-staged-commit-preview'
    base = b'original artifact'
    edited = b'approved staged artifact'
    base_hash = hashlib.sha256(base).hexdigest()
    edited_hash = hashlib.sha256(edited).hexdigest()
    operations = []
    patch_hash = compute_patch_hash(
        base_revision=1, base_sha256=base_hash, operations=operations, artifact_sha256=edited_hash
    )

    class MemoryStore:
        def __init__(self):
            self.objects = {'base-key': base, 'staged-key': edited}

        async def get(self, key):
            if key not in self.objects:
                raise OSError('staged object storage unavailable')
            return self.objects[key]

    store = MemoryStore()
    async with postgres_resource_db() as db:
        db.add(
            Document(
                id=document_id,
                owner_id='postgres-test',
                filename='staged.txt',
                mime_type='text/plain',
                current_revision=1,
            )
        )
        db.add(
            Revision(
                id='pg-staged-base-revision',
                document_id=document_id,
                number=1,
                object_key='base-key',
                sha256=base_hash,
                size=len(base),
            )
        )
        db.add(
            Preview(
                id=preview_id,
                document_id=document_id,
                owner_id='postgres-test',
                base_revision=1,
                base_sha256=base_hash,
                artifact_sha256=edited_hash,
                patch_hash=patch_hash,
                operations=operations,
                status='ready',
                preview_key='staged-key',
            )
        )
        await db.commit()

        approval = {
            'base_revision': 1,
            'base_sha256': base_hash,
            'artifact_sha256': edited_hash,
            'patch_hash': patch_hash,
        }
        revision, _ = await commit_preview(db, store, 'postgres-test', preview_id, approval=approval)
        assert revision.number == 2
        assert revision.object_key == 'staged-key'
        assert store.objects['staged-key'] == edited

        retry_revision, _ = await commit_preview(db, store, 'postgres-test', preview_id)
        assert retry_revision.id == revision.id

    missing_preview_id = 'pg-missing-staged-preview'
    missing_hash = hashlib.sha256(b'not-present').hexdigest()
    missing_patch_hash = compute_patch_hash(
        base_revision=2, base_sha256=edited_hash, operations=operations, artifact_sha256=missing_hash
    )
    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        db.add(
            Preview(
                id=missing_preview_id,
                document_id=document_id,
                owner_id='postgres-test',
                base_revision=2,
                base_sha256=edited_hash,
                artifact_sha256=missing_hash,
                patch_hash=missing_patch_hash,
                operations=operations,
                status='ready',
                preview_key='missing-staged-key',
            )
        )
        await db.commit()
        with pytest.raises(OSError, match='staged object storage unavailable'):
            await commit_preview(
                db,
                store,
                'postgres-test',
                missing_preview_id,
                approval={
                    'base_revision': 2,
                    'base_sha256': edited_hash,
                    'artifact_sha256': missing_hash,
                    'patch_hash': missing_patch_hash,
                },
            )
        await db.rollback()

    async with postgres_resource_db() as db:
        document = await db.get(Document, document_id)
        missing_preview = await db.get(Preview, missing_preview_id)
        revisions = (await db.execute(select(Revision).where(Revision.document_id == document_id))).scalars().all()
        assert document.current_revision == 2
        assert missing_preview.status == 'ready'
        assert sorted(revision.number for revision in revisions) == [1, 2]


async def _seed_revision_previews(session_factory, *, count=1):
    """Create immutable base/staged bytes and ready previews in the disposable schema."""
    base = b'postgres-base'
    base_hash = hashlib.sha256(base).hexdigest()
    objects = {'base-key': base}
    previews = []
    async with session_factory() as db:
        db.add(Document(id='race-doc', owner_id='owner-1', filename='race.txt',
                        mime_type='text/plain', current_revision=1))
        db.add(Revision(id='race-base', document_id='race-doc', number=1,
                        object_key='base-key', sha256=base_hash, size=len(base)))
        for index in range(count):
            content = f'approved-{index}'.encode()
            artifact_hash = hashlib.sha256(content).hexdigest()
            key = f'stage-{index}'
            objects[key] = content
            operations = [{'op': 'replace_text', 'find': 'base', 'replace': str(index)}]
            patch_hash = compute_patch_hash(
                base_revision=1, base_sha256=base_hash, operations=operations, artifact_sha256=artifact_hash,
            )
            preview = Preview(id=f'race-preview-{index}', document_id='race-doc', owner_id='owner-1',
                              base_revision=1, base_sha256=base_hash, artifact_sha256=artifact_hash,
                              patch_hash=patch_hash, operations=operations, status='ready', preview_key=key)
            db.add(preview)
            previews.append((preview.id, {
                'base_revision': 1, 'base_sha256': base_hash,
                'artifact_sha256': artifact_hash, 'patch_hash': patch_hash,
            }))
        await db.commit()
    return objects, previews


@pytest.mark.asyncio
async def test_concurrent_preview_commits_publish_only_one_revision_in_postgres(postgres_resource_db, monkeypatch):
    from fastapi import HTTPException
    from weai.documents.revision_service import commit_preview

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    objects, previews = await _seed_revision_previews(postgres_resource_db, count=2)

    class MemoryStore:
        async def get(self, key):
            return objects[key]

    async def commit(index):
        preview_id, approval = previews[index]
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, MemoryStore(), 'owner-1', preview_id,
                approval=approval, idempotency_key=f'concurrent-{index}',
            )

    results = await asyncio.gather(commit(0), commit(1), return_exceptions=True)
    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == len(failures) == 1
    assert successes[0][0].number == 2
    assert isinstance(failures[0], HTTPException) and failures[0].status_code == 409
    async with postgres_resource_db() as db:
        assert (await db.get(Document, 'race-doc')).current_revision == 2
        assert len(list((await db.scalars(select(Revision))).all())) == 2
        assert len(list((await db.scalars(select(RevisionApproval))).all())) == 1
        assert len(list((await db.scalars(select(RevisionOutbox))).all())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_permission_revocation_during_commit_blocks_publication_in_postgres(
    postgres_resource_db, monkeypatch, session_sharing
):
    from fastapi import HTTPException
    from sqlalchemy import delete
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    # With sharing disabled the access-grant helper opens its own session; keep
    # that session in the same disposable database/schema as the publication.
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_revision_previews(postgres_resource_db)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        db.add(AccessGrant(id='grant-1', resource_type='ar_document', resource_id='race-doc',
                           principal_type='user', principal_id='collaborator', permission='write', created_at=1))
        await db.commit()

    staged_read_started = asyncio.Event()
    allow_staged_read = asyncio.Event()

    class PausedStore:
        async def get(self, key):
            if key == 'stage-0':
                staged_read_started.set()
                await allow_staged_read.wait()
            return objects[key]

    async def commit():
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, PausedStore(), 'collaborator', previews[0][0],
                approval=previews[0][1], idempotency_key='revoked-during-commit',
            )

    task = asyncio.create_task(commit())
    try:
        await asyncio.wait_for(staged_read_started.wait(), timeout=10)
        async with postgres_resource_db() as db:
            await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))
            await db.commit()
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    finally:
        allow_staged_read.set()
    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(task, timeout=10)
    assert error.value.status_code == 404
    async with postgres_resource_db() as db:
        assert (await db.get(Document, 'race-doc')).current_revision == 1
        assert len(list((await db.scalars(select(Revision))).all())) == 1
        assert list((await db.scalars(select(RevisionApproval))).all()) == []
        assert list((await db.scalars(select(RevisionOutbox))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
@pytest.mark.parametrize('grant_scope', ['ar_document', 'ar_folder'])
async def test_permission_revoked_after_final_check_must_prevent_commit_in_postgres(
    postgres_resource_db, monkeypatch, session_sharing, grant_scope
):
    """A committed revocation before publication must not authorize a later commit."""
    from fastapi import HTTPException
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant, AccessGrants

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_revision_previews(postgres_resource_db)
    grant_resource_id = 'shared-folder' if grant_scope == 'ar_folder' else 'race-doc'
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        if grant_scope == 'ar_folder':
            db.add(Folder(id='shared-folder', owner_id='owner-1', name='Shared'))
            document = await db.get(Document, 'race-doc')
            document.folder_id = 'shared-folder'
        db.add(AccessGrant(id='grant-1', resource_type=grant_scope, resource_id=grant_resource_id,
                           principal_type='user', principal_id='collaborator', permission='write', created_at=1))
        await db.commit()

    last_check_passed = asyncio.Event()
    allow_publication = asyncio.Event()
    original_accessible = library.accessible_document
    checks = 0

    async def pause_after_final_check(*args, **kwargs):
        nonlocal checks
        document = await original_accessible(*args, **kwargs)
        checks += 1
        if checks == 2:
            last_check_passed.set()
            await allow_publication.wait()
        return document

    monkeypatch.setattr(library, 'accessible_document', pause_after_final_check)

    class MemoryStore:
        async def get(self, key):
            return objects[key]

    async def commit():
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, MemoryStore(), 'collaborator', previews[0][0],
                approval=previews[0][1], idempotency_key='revoked-after-check',
            )

    async def revoke():
        async with postgres_resource_db() as db:
            await AccessGrants.set_access_grants(grant_scope, grant_resource_id, [], db=db)

    task = asyncio.create_task(commit())
    revocation = None
    revoked_before_release = False
    try:
        await asyncio.wait_for(last_check_passed.wait(), timeout=10)
        revocation = asyncio.create_task(revoke())
        done, _ = await asyncio.wait({revocation}, timeout=1)
        if done:
            await revocation
            revoked_before_release = True
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if revocation is not None:
            revocation.cancel()
            await asyncio.gather(revocation, return_exceptions=True)
        raise
    finally:
        allow_publication.set()

    if not revoked_before_release:
        # A correct locking protocol may serialize publication before revoke.
        revision, _ = await asyncio.wait_for(task, timeout=10)
        assert revision.number == 2
        await asyncio.wait_for(revocation, timeout=10)
        return

    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(task, timeout=10)
    assert error.value.status_code == 404
    async with postgres_resource_db() as db:
        assert (await db.get(Document, 'race-doc')).current_revision == 1
        assert len(list((await db.scalars(select(Revision))).all())) == 1
        assert list((await db.scalars(select(RevisionApproval))).all()) == []
        assert list((await db.scalars(select(RevisionOutbox))).all()) == []


async def _seed_prototype_authorization_gate(session_factory):
    """Test-only gate: this does not install or enable any production authorization protocol."""
    from weai.models.access_grants import AccessGrant

    objects, previews = await _seed_revision_previews(session_factory)
    async with session_factory() as db:
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        await db.execute(text('CREATE TABLE phase0_auth_gate (id INTEGER PRIMARY KEY)'))
        await db.execute(text('INSERT INTO phase0_auth_gate (id) VALUES (1)'))
        db.add(AccessGrant(id='grant-1', resource_type='ar_document', resource_id='race-doc',
                           principal_type='user', principal_id='collaborator', permission='write', created_at=1))
        await db.commit()
    return objects, previews


async def _wait_for_database_lock(session_factory, pid):
    """Observe a live PostgreSQL lock wait instead of assuming a fixed scheduling delay."""
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory() as db:
            wait_type = await db.scalar(
                text('SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid'), {'pid': pid}
            )
        if wait_type == 'Lock':
            return
        await asyncio.sleep(0.02)
    raise AssertionError('Expected PostgreSQL lock wait was not observed')


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_prototype_publication_gate_serializes_revocation_after_commit(
    postgres_resource_db, monkeypatch, session_sharing
):
    from sqlalchemy import delete
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_prototype_authorization_gate(postgres_resource_db)
    checked = asyncio.Event()
    allow_publish = asyncio.Event()
    revoker_pid = asyncio.Future()
    original = library.accessible_document
    checks = 0

    async def pause_after_final_check(*args, **kwargs):
        nonlocal checks
        document = await original(*args, **kwargs)
        checks += 1
        if checks == 2:
            checked.set()
            await allow_publish.wait()
        return document

    monkeypatch.setattr(library, 'accessible_document', pause_after_final_check)

    class Store:
        async def get(self, key):
            return objects[key]

    async def publish():
        async with postgres_resource_db() as db:
            await db.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR SHARE'))
            return await commit_preview(
                db, Store(), 'collaborator', previews[0][0], approval=previews[0][1],
                idempotency_key='prototype-publish-first',
            )

    async def revoke():
        async with postgres_resource_db() as db:
            revoker_pid.set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            await db.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR UPDATE'))
            revision_before_revoke = (await db.get(Document, 'race-doc')).current_revision
            await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))
            await db.commit()
            return revision_before_revoke

    publisher = asyncio.create_task(publish())
    revoker = None
    try:
        await asyncio.wait_for(checked.wait(), timeout=10)
        revoker = asyncio.create_task(revoke())
        await _wait_for_database_lock(postgres_resource_db, await asyncio.wait_for(revoker_pid, timeout=10))
        allow_publish.set()
        revision, _ = await asyncio.wait_for(publisher, timeout=10)
        assert revision.number == 2
        assert await asyncio.wait_for(revoker, timeout=10) == 2
    finally:
        allow_publish.set()
        tasks = [task for task in (publisher, revoker) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_prototype_revocation_gate_serializes_publication_after_revoke(
    postgres_resource_db, monkeypatch, session_sharing
):
    from fastapi import HTTPException
    from sqlalchemy import delete
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_prototype_authorization_gate(postgres_resource_db)
    revoke_ready = asyncio.Event()
    allow_revoke = asyncio.Event()
    publisher_pid = asyncio.Future()

    class Store:
        async def get(self, key):
            return objects[key]

    async def revoke():
        async with postgres_resource_db() as db:
            await db.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR UPDATE'))
            await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))
            revoke_ready.set()
            await allow_revoke.wait()
            await db.commit()

    async def publish():
        async with postgres_resource_db() as db:
            publisher_pid.set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            await db.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR SHARE'))
            return await commit_preview(
                db, Store(), 'collaborator', previews[0][0], approval=previews[0][1],
                idempotency_key='prototype-revoke-first',
            )

    revoker = asyncio.create_task(revoke())
    publisher = None
    try:
        await asyncio.wait_for(revoke_ready.wait(), timeout=10)
        publisher = asyncio.create_task(publish())
        await _wait_for_database_lock(postgres_resource_db, await asyncio.wait_for(publisher_pid, timeout=10))
        allow_revoke.set()
        await asyncio.wait_for(revoker, timeout=10)
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(publisher, timeout=10)
        assert error.value.status_code == 404
        async with postgres_resource_db() as db:
            assert (await db.get(Document, 'race-doc')).current_revision == 1
            assert len(list((await db.scalars(select(Revision))).all())) == 1
            assert list((await db.scalars(select(RevisionApproval))).all()) == []
            assert list((await db.scalars(select(RevisionOutbox))).all()) == []
    finally:
        allow_revoke.set()
        tasks = [task for task in (publisher, revoker) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_admin_demoted_during_commit_must_prevent_publication_in_postgres(
    postgres_resource_db, monkeypatch, session_sharing
):
    """An authenticated role snapshot cannot outlive a committed demotion."""
    from fastapi import HTTPException
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant
    from weai.models.users import User, Users

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_revision_previews(postgres_resource_db)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: User.__table__.create(sync.connection(), checkfirst=True))
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        db.add(User(id='former-admin', email='admin@example.invalid', name='Test admin', role='admin',
                    created_at=1, updated_at=1, last_active_at=1))
        await db.commit()

    async with postgres_resource_db() as db:
        authenticated_role = (await db.get(User, 'former-admin')).role
    assert authenticated_role == 'admin'
    staged_read_started = asyncio.Event()
    allow_staged_read = asyncio.Event()

    class PausedStore:
        async def get(self, key):
            if key == 'stage-0':
                staged_read_started.set()
                await allow_staged_read.wait()
            return objects[key]

    async def commit():
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, PausedStore(), 'former-admin', previews[0][0],
                is_admin=authenticated_role == 'admin', approval=previews[0][1],
                idempotency_key='demoted-admin',
            )

    async def demote():
        async with postgres_resource_db() as db:
            updated = await Users.update_user_role_by_id('former-admin', 'user', db=db)
            assert updated.role == 'user'

    task = asyncio.create_task(commit())
    demotion = None
    demoted_before_release = False
    try:
        await asyncio.wait_for(staged_read_started.wait(), timeout=10)
        demotion = asyncio.create_task(demote())
        done, _ = await asyncio.wait({demotion}, timeout=1)
        if done:
            await demotion
            demoted_before_release = True
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if demotion is not None:
            demotion.cancel()
            await asyncio.gather(demotion, return_exceptions=True)
        raise
    finally:
        allow_staged_read.set()

    if not demoted_before_release:
        # A correct implementation can serialize publication before demotion.
        revision, _ = await asyncio.wait_for(task, timeout=10)
        assert revision.number == 2
        await asyncio.wait_for(demotion, timeout=10)
        return

    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(task, timeout=10)
    assert error.value.status_code == 404
    async with postgres_resource_db() as db:
        assert (await db.get(User, 'former-admin')).role == 'user'
        assert (await db.get(Document, 'race-doc')).current_revision == 1
        assert len(list((await db.scalars(select(Revision))).all())) == 1
        assert list((await db.scalars(select(RevisionApproval))).all()) == []
        assert list((await db.scalars(select(RevisionOutbox))).all()) == []


@pytest.mark.asyncio
async def test_document_move_rechecks_admin_role_after_demotion_in_postgres(postgres_resource_db, monkeypatch):
    """A route's old admin flag must not authorize a later folder move."""
    from fastapi import HTTPException
    from weai.documents import library
    from weai.models.access_grants import AccessGrant
    from weai.models.users import User, Users

    from weai.internal import db as internal_db

    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        db.add(User(id='former-admin', email='admin@example.invalid', name='Former admin', role='admin',
                    created_at=1, updated_at=1, last_active_at=1))
        db.add(Folder(id='move-source', owner_id='owner-1', name='Source', is_personal_root=False))
        db.add(Folder(id='move-target', owner_id='owner-1', name='Private', is_personal_root=False))
        await db.flush()
        db.add(Document(id='move-doc', owner_id='owner-1', folder_id='move-source',
                        filename='report.xlsx', mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        current_revision=1))
        await db.commit()

    async with postgres_resource_db() as db:
        authenticated_is_admin = (await db.get(User, 'former-admin')).role == 'admin'
    assert authenticated_is_admin

    async with postgres_resource_db() as db:
        updated = await Users.update_user_role_by_id('former-admin', 'user', db=db)
        assert updated.role == 'user'

    async with postgres_resource_db() as db:
        with pytest.raises(HTTPException) as error:
            await library.move_document(db, 'move-doc', 'former-admin', 'move-target',
                                        is_admin=authenticated_is_admin)
        assert error.value.status_code == 404
        await db.rollback()
        assert (await db.get(Document, 'move-doc')).folder_id == 'move-source'


@pytest.mark.asyncio
@pytest.mark.parametrize(('isolation', 'expected_count'), [
    ('READ COMMITTED', 0),
    ('REPEATABLE READ', 1),
])
async def test_prototype_gate_does_not_refresh_an_existing_repeatable_read_snapshot(
    postgres_resource_db, isolation, expected_count
):
    """A gate row lock alone cannot refresh an earlier transaction snapshot."""
    from sqlalchemy import delete, func
    from weai.models.access_grants import AccessGrant

    await _seed_prototype_authorization_gate(postgres_resource_db)
    async with postgres_resource_db() as reader:
        await reader.execute(text(f'SET TRANSACTION ISOLATION LEVEL {isolation}'))
        assert await reader.scalar(select(func.count()).select_from(AccessGrant)) == 1
        async with postgres_resource_db() as writer:
            await writer.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR UPDATE'))
            await writer.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))
            await writer.commit()
        # Writer has committed before the reader acquires the proposed gate.
        await reader.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR SHARE'))
        assert await reader.scalar(select(func.count()).select_from(AccessGrant)) == expected_count


@pytest.mark.asyncio
@pytest.mark.parametrize(('isolation', 'expected_count'), [
    ('READ COMMITTED', 0),
    ('REPEATABLE READ', 1),
])
async def test_prototype_gate_first_query_waits_on_revoker_with_transaction_snapshot(
    postgres_resource_db, isolation, expected_count
):
    """Under repeatable read, a waiting first lock query can pin a stale snapshot."""
    from sqlalchemy import delete, func
    from weai.models.access_grants import AccessGrant

    await _seed_prototype_authorization_gate(postgres_resource_db)
    reader_pid = asyncio.get_running_loop().create_future()
    async with postgres_resource_db() as writer:
        await writer.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR UPDATE'))
        await writer.execute(delete(AccessGrant).where(AccessGrant.id == 'grant-1'))

        async def read_after_gate():
            async with postgres_resource_db() as reader:
                await reader.execute(text(f'SET TRANSACTION ISOLATION LEVEL {isolation}'))
                connection = await reader.connection()
                raw = await connection.get_raw_connection()
                reader_pid.set_result(raw.driver_connection.info.backend_pid)
                # This is the first SELECT in the reader transaction.
                await reader.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR SHARE'))
                return await reader.scalar(select(func.count()).select_from(AccessGrant))

        task = asyncio.create_task(read_after_gate())
        try:
            await _wait_for_database_lock(postgres_resource_db, await asyncio.wait_for(reader_pid, timeout=10))
            await writer.commit()
            assert await asyncio.wait_for(task, timeout=10) == expected_count
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_prototype_gate_requires_fresh_orm_role_read_in_read_committed(postgres_resource_db, monkeypatch):
    """A reused identity-map entry can outlive a committed role change."""
    from sqlalchemy import update
    from weai.internal import db as internal_db
    from weai.models.users import User, Users

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)
    await _seed_prototype_authorization_gate(postgres_resource_db)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: User.__table__.create(sync.connection(), checkfirst=True))
        db.add(User(id='former-admin', email='admin@example.invalid', name='Test admin', role='admin',
                    created_at=1, updated_at=1, last_active_at=1))
        await db.commit()

    async with postgres_resource_db() as reader:
        await reader.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED'))
        cached = await reader.get(User, 'former-admin')
        assert cached.role == 'admin'
        async with postgres_resource_db() as writer:
            await writer.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR UPDATE'))
            await writer.execute(update(User).where(User.id == 'former-admin').values(role='user'))
            await writer.commit()
        await reader.execute(text('SELECT id FROM phase0_auth_gate WHERE id = 1 FOR SHARE'))
        stale = await Users.get_user_by_id('former-admin', db=reader)
        assert stale.role == 'admin'
        fresh = await reader.scalar(select(User).where(User.id == 'former-admin').execution_options(populate_existing=True))
        assert fresh.role == 'user'


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_owner_deactivated_during_commit_must_prevent_publication_in_postgres(
    postgres_resource_db, monkeypatch, session_sharing
):
    """An authenticated owner cannot publish after the account is deactivated."""
    from fastapi import HTTPException
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant
    from weai.models.users import User, Users

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)

    async def no_groups(_db, _user_id):
        return []

    monkeypatch.setattr(library, '_groups_for', no_groups)
    objects, previews = await _seed_revision_previews(postgres_resource_db)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: User.__table__.create(sync.connection(), checkfirst=True))
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))

    async with postgres_resource_db() as db:
        authenticated_role = (await db.get(User, 'owner-1')).role
    assert authenticated_role == 'user'
    staged_read_started = asyncio.Event()
    allow_staged_read = asyncio.Event()

    class PausedStore:
        async def get(self, key):
            if key == 'stage-0':
                staged_read_started.set()
                await allow_staged_read.wait()
            return objects[key]

    async def commit():
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, PausedStore(), 'owner-1', previews[0][0],
                is_admin=False, approval=previews[0][1],
                idempotency_key='deactivated-owner',
            )

    async def demote():
        async with postgres_resource_db() as db:
            updated = await Users.update_user_by_id('owner-1', {'role': 'pending'}, db=db)
            assert updated.role == 'pending'

    task = asyncio.create_task(commit())
    demotion = None
    demoted_before_release = False
    try:
        await asyncio.wait_for(staged_read_started.wait(), timeout=10)
        demotion = asyncio.create_task(demote())
        done, _ = await asyncio.wait({demotion}, timeout=1)
        if done:
            await demotion
            demoted_before_release = True
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if demotion is not None:
            demotion.cancel()
            await asyncio.gather(demotion, return_exceptions=True)
        raise
    finally:
        allow_staged_read.set()

    if not demoted_before_release:
        # A correct implementation can serialize publication before demotion.
        revision, _ = await asyncio.wait_for(task, timeout=10)
        assert revision.number == 2
        await asyncio.wait_for(demotion, timeout=10)
        return

    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(task, timeout=10)
    assert error.value.status_code in (401, 403, 404)
    async with postgres_resource_db() as db:
        assert (await db.get(User, 'owner-1')).role == 'pending'
        assert (await db.get(Document, 'race-doc')).current_revision == 1
        assert len(list((await db.scalars(select(Revision))).all())) == 1
        assert list((await db.scalars(select(RevisionApproval))).all()) == []
        assert list((await db.scalars(select(RevisionOutbox))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('session_sharing', [True, False])
async def test_group_membership_revoked_after_check_serializes_publication_in_postgres(
    postgres_resource_db, monkeypatch, session_sharing
):
    from fastapi import HTTPException
    from weai.internal import db as internal_db
    from weai.documents import library
    from weai.documents.revision_service import commit_preview
    from weai.models.access_grants import AccessGrant
    from weai.models.groups import Group, GroupMember, Groups

    monkeypatch.setenv('AGENT_RUNTIME_RESOURCE_INDEX_WORKER_ENABLED', 'true')
    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', session_sharing)
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', postgres_resource_db)
    objects, previews = await _seed_revision_previews(postgres_resource_db)
    async with postgres_resource_db() as db:
        await db.run_sync(lambda sync: Group.__table__.create(sync.connection(), checkfirst=True))
        await db.run_sync(lambda sync: GroupMember.__table__.create(sync.connection(), checkfirst=True))
        await db.run_sync(lambda sync: AccessGrant.__table__.create(sync.connection(), checkfirst=True))
        db.add_all([
            Group(id='writers', user_id='owner-1', name='Writers', description='',
                  created_at=1, updated_at=1),
            GroupMember(id='member-1', group_id='writers', user_id='collaborator',
                        created_at=1, updated_at=1),
            AccessGrant(id='group-grant', resource_type='ar_document', resource_id='race-doc',
                        principal_type='group', principal_id='writers', permission='write', created_at=1),
        ])
        await db.commit()

    checked = asyncio.Event()
    release = asyncio.Event()
    original = library.accessible_document
    calls = 0

    async def pause_after_final_check(*args, **kwargs):
        nonlocal calls
        document = await original(*args, **kwargs)
        calls += 1
        if calls == 2:
            checked.set()
            await release.wait()
        return document

    monkeypatch.setattr(library, 'accessible_document', pause_after_final_check)

    class Store:
        async def get(self, key):
            return objects[key]

    async def publish():
        async with postgres_resource_db() as db:
            return await commit_preview(
                db, Store(), 'collaborator', previews[0][0], approval=previews[0][1],
                idempotency_key='group-revoke-race',
            )

    publisher = asyncio.create_task(publish())
    revocation = None
    revoked_first = False
    try:
        await asyncio.wait_for(checked.wait(), timeout=10)
        revocation = asyncio.create_task(Groups.set_group_user_ids_by_id('writers', []))
        done, _ = await asyncio.wait({revocation}, timeout=1)
        if done:
            await revocation
            revoked_first = True
    finally:
        release.set()

    if revoked_first:
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(publisher, timeout=10)
        assert error.value.status_code == 404
    else:
        revision, _ = await asyncio.wait_for(publisher, timeout=10)
        assert revision.number == 2
        await asyncio.wait_for(revocation, timeout=10)

    async with postgres_resource_db() as db:
        assert (await db.scalar(select(Document.current_revision).where(Document.id == 'race-doc'))) == (
            1 if revoked_first else 2
        )


@pytest.mark.asyncio
async def test_authorization_gate_rejects_repeatable_read_snapshot(postgres_resource_db):
    from weai.internal.db import lock_document_authorization

    async with postgres_resource_db() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        with pytest.raises(RuntimeError, match='READ COMMITTED'):
            await lock_document_authorization(db, exclusive=False)
