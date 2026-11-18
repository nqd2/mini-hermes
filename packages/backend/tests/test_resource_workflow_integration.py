from contextlib import asynccontextmanager
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.agent_runtime.execution.pipeline import ResourceRequestPipeline
from weai.model_routing.model_capabilities import ModelCapabilities
from weai.documents.models import Document, DocumentChunk, Folder, ResourceIndexJob, Revision
from weai.model_routing.provider_guard import validate_chat_payload
from weai.documents.resource_indexing import process_resource_index_job
from weai.documents.resource_service import search_chat_files
from weai.documents.service import create_document
from weai.utils.chat_runtime.citations import get_citation_source_from_tool_result
from weai.utils.chat_runtime.resource_execution import ResourceAnswerGate
from weai.utils.json_codec import JSONCodec


@pytest.mark.asyncio
async def test_upload_to_bounded_answer_preserves_evidence_and_provider_budget(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Document.metadata.create_all(
                sync,
                tables=[
                    Folder.__table__,
                    Document.__table__,
                    Revision.__table__,
                    ResourceIndexJob.__table__,
                    DocumentChunk.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Store:
        def __init__(self):
            self.objects = {}

        async def put(self, key, content, _mime_type):
            self.objects[key] = content

        async def get(self, key):
            return self.objects[key]

    store = Store()
    async with sessions() as db:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Programs'
        sheet.append(['Program', 'Field'])
        for row_number in range(2, 301):
            sheet.append([f'Program {row_number}', 'General studies'])
        sheet.append(['Program 301', 'Computer Science'])
        content = BytesIO()
        workbook.save(content)
        doc = await create_document(db, store, 'alice', 'programs.xlsx', content.getvalue())
        index_job = (
            await db.execute(
                select(ResourceIndexJob).where(
                    ResourceIndexJob.document_id == doc.id,
                    ResourceIndexJob.revision == doc.current_revision,
                )
            )
        ).scalar_one()
        assert await process_resource_index_job(db, index_job.id, store)

    @asynccontextmanager
    async def open_db():
        async with sessions() as db:
            yield db

    async def register_upload(_db, user_id, file_id, _store):
        assert user_id == 'alice'
        return doc if file_id == 'upload-1' else None

    monkeypatch.setattr('weai.documents.resource_service.get_async_db', open_db)
    monkeypatch.setattr('weai.documents.resource_service.ObjectStore', lambda: store)
    monkeypatch.setattr('weai.documents.resource_service.register_chat_upload', register_upload)

    prompt = 'Which program is related to Computer Science?'
    metadata = {
        'resource_history_files': [{'type': 'file', 'id': 'upload-1', 'name': 'programs.xlsx'}],
        'user_message': {'files': [{'type': 'file', 'id': 'upload-1', 'name': 'programs.xlsx'}]},
    }
    pipeline = ResourceRequestPipeline(search_chat_files)
    pipeline.resolve(metadata, prompt, ModelCapabilities(supports_tools=False), pending_task_ids=[])
    sources = await pipeline.read('alice', metadata, prompt)

    assert sources, {
        'reason': metadata['agent_task_state'].get('reason'),
        'scope': metadata.get('resource_scope_ids'),
        'requested': metadata.get('resource_evidence_requested'),
    }
    assert any('Computer Science' in text for source in sources for text in source['document']), [
        (source['document'][0][:80], source['metadata'][0].get('rows')) for source in sources
    ]
    evidence = sources[0]['metadata'][0]
    assert evidence['revision'] == 1
    assert evidence['evidence_id']
    assert 301 in evidence['rows']
    assert pipeline.decision(metadata).status == 'completed'

    payload = {
        'model': 'qwen3.7-plus',
        'messages': [{'role': 'user', 'content': prompt + '\n' + JSONCodec.dumps(sources)}],
    }
    wire_body = JSONCodec.dumps(payload, ensure_ascii=False)
    metrics = validate_chat_payload(wire_body, payload, 'https://api.1min.ai/openai/v1')
    assert metrics['serialized_chars'] == len(wire_body)
    assert metrics['serialized_chars'] < 95_000

    citation_result = {
        'document_id': evidence['document_id'],
        'filename': 'programs.xlsx',
        'revision': evidence['revision'],
        'results': [
            {
                'text': sources[0]['document'][0],
                'evidence_id': evidence['evidence_id'],
                'start_offset': evidence['start_offset'],
                'end_offset': evidence['end_offset'],
            }
        ],
    }
    citation = get_citation_source_from_tool_result('search_editable_document', {}, JSONCodec.dumps(citation_result))
    assert citation[0]['metadata'][0]['evidence_id'] == evidence['evidence_id']
    assert citation[0]['metadata'][0]['revision'] == evidence['revision']

    answer = {'type': 'message', 'role': 'assistant', 'content': 'Program 301'}
    assert ResourceAnswerGate(metadata, enabled=True).present([answer], final=True) == [answer]
    await engine.dispose()
