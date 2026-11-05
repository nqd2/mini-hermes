import asyncio
import base64
import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from docx import Document as WordDocument
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes import worker
from open_webui.mini_hermes.drive import DriveClient, DriveError, sheet_range
from open_webui.mini_hermes.models import Document, DriveCredential, Folder, Preview, Revision, SyncJob, now
from open_webui.mini_hermes.oauth import save_refresh_token
from open_webui.mini_hermes.service import commit_preview, create_document, create_preview, owned_document


class MemoryStore:
    def __init__(self):
        self.objects = {}

    async def put(self, key, content, mime_type):
        self.objects[key] = content

    async def get(self, key):
        return self.objects[key]


@pytest.mark.asyncio
async def test_native_sheets_uses_quoted_ranges_and_literal_values():
    assert sheet_range("Sales team's 2024", 'A1') == "'Sales team''s 2024'!A1"
    client = DriveClient('fake-token')
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))

    client.request = request
    try:
        await client.apply_native('sheet-id', 'application/vnd.google-apps.spreadsheet', [
            {'op': 'set_cell', 'sheet': 'Sales 2024', 'cell': 'A1', 'value': '00123'},
            {'op': 'append_row', 'sheet': 'Sales 2024', 'values': ['00456']},
        ])
    finally:
        await client.close()
    assert "'Sales 2024'!A1" in calls[0][1]
    assert "'Sales 2024'!A:ZZ" in calls[1][1]
    assert all(call[2]['params']['valueInputOption'] == 'RAW' for call in calls)


@pytest.mark.asyncio
async def test_native_docs_write_requires_expected_revision():
    client = DriveClient('fake-token')
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))

    client.request = request
    try:
        await client.apply_native('doc-id', 'application/vnd.google-apps.document', [
            {'op': 'append_paragraph', 'text': 'Hello'},
        ], expected_revision='revision-1')
    finally:
        await client.close()
    assert calls[0][2]['json']['writeControl'] == {'requiredRevisionId': 'revision-1'}


@pytest.mark.asyncio
async def test_drive_download_stops_after_byte_limit():
    client = DriveClient('fake-token')

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield b'1234'
            yield b'5678'

    class HttpClient:
        def stream(self, *args, **kwargs):
            return Response()

        async def aclose(self):
            return None

    client.client = HttpClient()
    try:
        with pytest.raises(DriveError, match='exceeds 5 byte limit'):
            await client.request_content_limited('GET', 'https://example.invalid/file', 5)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_preview_commit_is_idempotent_and_owned():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Folder.__table__, Document.__table__, Revision.__table__, Preview.__table__, SyncJob.__table__, DriveCredential.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MemoryStore()
    from io import BytesIO

    word = WordDocument()
    word.add_paragraph('Before')
    data = BytesIO()
    word.save(data)
    async with sessions() as db:
        doc = await create_document(db, store, 'owner', 'test.docx', data.getvalue())
        assert doc.mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        with pytest.raises(HTTPException) as error:
            await owned_document(db, doc.id, 'other')
        assert error.value.status_code == 404
        preview = await create_preview(db, doc, 'owner', [{'op': 'replace_text', 'find': 'Before', 'replace': 'After'}])
        preview.preview_key = f'documents/{doc.id}/previews/{preview.id}'
        preview.status = 'ready'
        await store.put(preview.preview_key, data.getvalue(), doc.mime_type)
        await db.commit()
        first_revision, first_job = await commit_preview(db, store, 'owner', preview.id)
        second_revision, second_job = await commit_preview(db, store, 'owner', preview.id)
        assert first_revision.id == second_revision.id
        assert first_job is second_job is None
        assert first_job is None  # Personal Folder revisions stay local until Drive sync is implemented.
        assert doc.current_revision == 2
        assert len((await db.execute(select(SyncJob))).scalars().all()) == 0

        doc.drive_file_id = 'drive-file-1'
        await db.commit()
        preview = await create_preview(db, doc, 'owner', [{'op': 'replace_text', 'find': 'After', 'replace': 'Synced'}])
        preview.preview_key = f'documents/{doc.id}/previews/{preview.id}'
        preview.status = 'ready'
        await store.put(preview.preview_key, data.getvalue(), doc.mime_type)
        await db.commit()
        _, drive_job = await commit_preview(db, store, 'owner', preview.id)
        assert drive_job is not None and drive_job.status == 'pending'
        assert len((await db.execute(select(SyncJob))).scalars().all()) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_drive_refresh_tokens_are_encrypted_per_user(monkeypatch):
    monkeypatch.setenv('MH_TOKEN_ENCRYPTION_KEY', 'test-only-key')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: DriveCredential.__table__.create(sync))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        await save_refresh_token(db, 'alice', 'alice-refresh-token')
        await save_refresh_token(db, 'bob', 'bob-refresh-token')
        credentials = (await db.execute(select(DriveCredential))).scalars().all()
        assert {credential.user_id for credential in credentials} == {'alice', 'bob'}
        assert all(b'refresh-token' not in credential.encrypted_refresh_token for credential in credentials)
        assert credentials[0].encrypted_refresh_token != credentials[1].encrypted_refresh_token
    await engine.dispose()


@pytest.mark.asyncio
async def test_drive_version_conflict_keeps_internal_revision(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Document.__table__, Revision.__table__, Preview.__table__, SyncJob.__table__, DriveCredential.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id, revision_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add(Document(id=doc_id, owner_id='owner', filename='test.docx', mime_type='application/test',
                        current_revision=2, drive_file_id='drive-123', drive_mime_type='application/test', drive_version='1'))
        db.add(Revision(id=revision_id, document_id=doc_id, number=2, object_key='test',
                        sha256=hashlib.sha256(b'changed').hexdigest(), size=7))
        db.add(SyncJob(id=job_id, document_id=doc_id, revision_id=revision_id, status='processing', attempts=1))
        await db.commit()

    async def token_for(db, user_id):
        return 'fake-token'

    class FakeGateway:
        async def call(self, route, **kwargs):
            if route == 'metadata':
                return {'id': 'drive-123', 'version': '2', 'capabilities': {'canEdit': True}}
            if route == 'download':
                return {'content_b64': base64.b64encode(b'collaborator changed').decode()}
            raise AssertionError('Drive write must not occur after a version conflict')

    store = MemoryStore()
    store.objects['test'] = b'changed'
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'access_token_for', token_for)
    monkeypatch.setattr(worker, 'Gateway', FakeGateway)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: store)
    await worker.process_sync(job_id)
    async with sessions() as db:
        job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
        revision = (await db.execute(select(Revision).where(Revision.id == revision_id))).scalar_one()
        assert job.status == 'failed'
        assert 'Drive conflict' in job.error
        assert revision.size == 7
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_jobs_wait_for_earlier_revision(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Document.__table__, Revision.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id, first_revision, second_revision = str(uuid4()), str(uuid4()), str(uuid4())
    first_job, second_job = str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add(Document(id=doc_id, owner_id='owner', filename='test.docx', mime_type='application/test', current_revision=3))
        db.add_all([
            Revision(id=first_revision, document_id=doc_id, number=2, object_key='first', sha256='x', size=1),
            Revision(id=second_revision, document_id=doc_id, number=3, object_key='second', sha256='y', size=1),
            SyncJob(id=first_job, document_id=doc_id, revision_id=first_revision, status='failed',
                    attempts=1, next_attempt_at=now() + timedelta(hours=1)),
            SyncJob(id=second_job, document_id=doc_id, revision_id=second_revision, status='pending'),
        ])
        await db.commit()
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    assert await worker.claim_sync() is None
    async with sessions() as db:
        job = (await db.execute(select(SyncJob).where(SyncJob.id == first_job))).scalar_one()
        job.next_attempt_at = now() - timedelta(seconds=1)
        await db.commit()
    assert await worker.claim_sync() == first_job
    async with sessions() as db:
        job = (await db.execute(select(SyncJob).where(SyncJob.id == first_job))).scalar_one()
        job.status = 'synced'
        await db.commit()
    assert await worker.claim_sync() == second_job
    await engine.dispose()


@pytest.mark.asyncio
async def test_drive_write_recovery_recognizes_uploaded_bytes(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Document.__table__, Revision.__table__, SyncJob.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id, revision_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add(Document(id=doc_id, owner_id='owner', filename='test.docx', mime_type='application/test',
                        current_revision=2, drive_file_id='drive-123', drive_mime_type='application/test', drive_version='1'))
        db.add(Revision(id=revision_id, document_id=doc_id, number=2, object_key='test',
                        sha256=hashlib.sha256(b'changed').hexdigest(), size=7))
        db.add(SyncJob(id=job_id, document_id=doc_id, revision_id=revision_id, status='processing', attempts=2))
        await db.commit()

    async def token_for(db, user_id):
        return 'fake-token'

    class FakeGateway:
        async def call(self, route, **kwargs):
            if route == 'metadata':
                return {'id': 'drive-123', 'version': '2', 'capabilities': {'canEdit': True}}
            if route == 'download':
                return {'content_b64': base64.b64encode(b'changed').decode()}
            raise AssertionError('Already uploaded content must not be written twice')

    store = MemoryStore()
    store.objects['test'] = b'changed'
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'access_token_for', token_for)
    monkeypatch.setattr(worker, 'Gateway', FakeGateway)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: store)
    await worker.process_sync(job_id)
    async with sessions() as db:
        doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one()
        job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
        assert doc.drive_version == '2' and job.status == 'synced'
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_drive_sync_keeps_original_and_creates_office_companion(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Document.metadata.create_all(
            sync, tables=[Document.__table__, Revision.__table__, Preview.__table__, SyncJob.__table__, DriveCredential.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id, revision_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add(Document(id=doc_id, owner_id='owner', filename='report.docx', mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        current_revision=2, drive_file_id='native-123',
                        drive_mime_type='application/vnd.google-apps.document', drive_version='1'))
        db.add(Revision(id=revision_id, document_id=doc_id, number=2, object_key='test',
                        sha256=hashlib.sha256(b'changed').hexdigest(), size=7,
                        operations=[{'op': 'replace_text', 'find': 'Before', 'replace': 'After'}]))
        db.add(SyncJob(id=job_id, document_id=doc_id, revision_id=revision_id, status='processing', attempts=1))
        await db.commit()

    async def token_for(db, user_id):
        assert user_id == 'owner'
        return 'fake-token'

    calls = []

    class FakeGateway:
        async def call(self, route, **kwargs):
            calls.append((route, kwargs))
            if route == 'metadata':
                return {'id': 'native-123', 'version': '1' if len([x for x in calls if x[0] == 'metadata']) <= 2 else '2',
                        'capabilities': {'canEdit': True}, 'parents': ['folder-1']}
            if route == 'native-revision':
                return {'revision_id': 'docs-revision-1'}
            if route == 'upload':
                return {'id': 'office-999'}
            if route == 'find-created':
                return {'file': None}
            if route == 'apply-native':
                return {'status': 'ok'}
            raise AssertionError(route)

    store = MemoryStore()
    store.objects['test'] = b'changed'
    monkeypatch.setattr(worker, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(worker, 'access_token_for', token_for)
    monkeypatch.setattr(worker, 'Gateway', FakeGateway)
    monkeypatch.setattr(worker, 'ObjectStore', lambda: store)
    await worker.process_sync(job_id)
    async with sessions() as db:
        doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one()
        job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
        assert (doc.drive_file_id, doc.office_companion_id, doc.drive_version) == ('native-123', 'office-999', '2')
        assert job.status == 'synced'
        assert [route for route, _ in calls] == ['metadata', 'native-revision', 'metadata', 'apply-native', 'find-created', 'upload', 'metadata']
        assert next(kwargs['expected_revision'] for route, kwargs in calls if route == 'apply-native') == 'docs-revision-1'
    await engine.dispose()
