from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import hashlib
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime.files import AgentFileContentStore, LargeFileReader
from open_webui.agent_runtime.models import AgentFileContent, Base
from open_webui.models.files import File, Files


class LocalObjectStore:
    def __init__(self):
        self.values: dict[str, bytes] = {}
        self.max_range = 0

    async def put(self, key: str, content: bytes, mime_type: str):
        self.values[key] = content

    async def get_range(self, key: str, offset: int, length: int) -> bytes:
        self.max_range = max(self.max_range, length)
        return self.values[key][offset : offset + length]

    async def delete(self, key: str):
        self.values.pop(key, None)


class CorruptingObjectStore(LocalObjectStore):
    async def get_range(self, key: str, offset: int, length: int) -> bytes:
        data = await super().get_range(key, offset, length)
        return (bytes([data[0] ^ 1]) + data[1:]) if data and offset == 0 else data


@pytest.mark.asyncio
async def test_large_file_reader_supports_metadata_line_byte_range_and_search(tmp_path):
    path = tmp_path / 'large.txt'
    content = ('alpha\nneedle here\nomega\n' * 450_000).encode()
    path.write_bytes(content)
    reader = LargeFileReader(allowed_roots=[tmp_path], chunk_size=32 * 1024)

    metadata = await reader.metadata(path)
    lines = await reader.read_lines(path, 2, 3)
    byte_range = await reader.read_bytes(path, 7, 12)
    matches = await reader.search(path, 'needle', max_matches=3)

    assert metadata.size_bytes > 10_000_000
    assert metadata.line_count == 1_350_000
    assert lines == [(2, 'needle here'), (3, 'omega')]
    assert byte_range == content[7:19]
    assert [entry.line_number for entry in matches] == [2, 5, 8]


@pytest.mark.asyncio
async def test_large_file_reader_rejects_paths_outside_allowed_roots(tmp_path):
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('secret')
    reader = LargeFileReader(allowed_roots=[allowed])

    with pytest.raises(PermissionError):
        await reader.metadata(outside)


@pytest.mark.asyncio
async def test_large_file_reader_indexes_hundred_thousand_files_lazily(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    for index in range(100_000):
        (root / f'file-{index:06}.txt').touch()
    reader = LargeFileReader(allowed_roots=[root])

    entries = reader.iter_files(root)
    first = [next(entries) for _ in range(5)]

    assert len(first) == 5
    assert first == sorted(first)


@pytest.mark.asyncio
async def test_file_content_migration_verifies_object_before_removing_legacy_copy(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "agent-files.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    content = ('α line\nsecond line\n' * 30_000) + 'tail'
    async with factory() as db:
        db.add(
            File(
                id='file-1', user_id='owner-1', filename='large.txt', path=None,
                data={'status': 'completed', 'content': content}, meta={'content_type': 'text/plain'},
                created_at=1, updated_at=1,
            )
        )
        await db.commit()
    object_store = LocalObjectStore()
    files = AgentFileContentStore(factory, object_store, inline_threshold_bytes=1)

    reference = await files.migrate_file('file-1', owner_id='owner-1')
    async with factory() as db:
        row = await db.get(File, 'file-1')
        stored = await db.get(AgentFileContent, 'file-1')

    assert reference.sha256 == stored.sha256
    assert object_store.values[reference.object_key] == content.encode('utf-8')
    assert 'content' not in row.data
    assert row.data['status'] == 'completed'

    start = content.index('second line')
    excerpt, total_chars, first_line = await files.read_chars(reference, start, 24)
    assert excerpt == content[start : start + 24]
    assert total_chars == len(content)
    assert first_line == content[:start].count('\n') + 1

    selected = await files.read_lines(reference, 2, 3)
    assert selected == [(2, 'second line', 7, 18), (3, 'α line', 19, 25)]
    indexed_lines = await files.read_lines(reference, 130, 131)
    assert indexed_lines == [
        (130, 'second line', content.index('second line', 0) + (len('α line\nsecond line\n') * 64),
         content.index('second line', 0) + (len('α line\nsecond line\n') * 64) + len('second line')),
        (131, 'α line', len('α line\nsecond line\n') * 65, len('α line\nsecond line\n') * 65 + len('α line')),
    ]
    for offset in (4095, 4096, 4097, 12345, len(content) - 12):
        chunk, _, _ = await files.read_chars(reference, offset, 17)
        assert chunk == content[offset : offset + 17]
    assert object_store.max_range <= files.chunk_bytes

    # Existing file-model consumers still receive the original content via the adapter.
    monkeypatch.setattr(AgentFileContentStore, 'configured', classmethod(lambda cls: files))
    monkeypatch.setattr(Files, '_external_content_store', files)
    import open_webui.models.files as files_model

    async with factory() as db:
        @asynccontextmanager
        async def use_test_session(_provided=None):
            yield db

        monkeypatch.setattr(files_model, 'get_async_db_context', use_test_session)
        hydrated = await Files.get_file_by_id('file-1')
    assert hydrated.data['content'] == content
    await engine.dispose()


@pytest.mark.asyncio
async def test_file_content_migration_keeps_source_when_object_checksum_fails(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "agent-files-corrupt.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    content = 'source text\n' * 2000
    async with factory() as db:
        db.add(File(
            id='file-corrupt', user_id='owner-1', filename='source.txt', path=None,
            data={'content': content}, meta={}, created_at=1, updated_at=1,
        ))
        await db.commit()
    storage = CorruptingObjectStore()
    files = AgentFileContentStore(factory, storage, chunk_bytes=1024)

    with pytest.raises(IOError, match='checksum'):
        await files.migrate_file('file-corrupt', owner_id='owner-1')

    async with factory() as db:
        source = await db.get(File, 'file-corrupt')
        reference = await db.get(AgentFileContent, 'file-corrupt')
    assert source.data['content'] == content
    assert reference is None
    assert storage.values == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_view_file_uses_object_ranges_after_verified_legacy_migration(tmp_path, monkeypatch):
    from starlette.requests import Request

    from open_webui.models.files import FileModel
    from open_webui.tools.builtin import view_file
    from open_webui.utils.json_codec import JSONCodec

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "agent-file-view.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    content = ('first line\nsecond line\nthird line\n' * 20_000) + 'last line'
    async with factory() as db:
        db.add(File(
            id='file-view', user_id='owner-1', filename='view.txt', path=None,
            data={'content': content}, meta={'content_type': 'text/plain'}, created_at=1, updated_at=1,
        ))
        await db.commit()
    storage = LocalObjectStore()
    store = AgentFileContentStore(factory, storage, inline_threshold_bytes=1, chunk_bytes=4096)
    monkeypatch.setattr(AgentFileContentStore, 'configured', classmethod(lambda cls: store))
    header = FileModel(
        id='file-view', user_id='owner-1', filename='view.txt', path=None, data={},
        meta={'content_type': 'text/plain'}, created_at=1, updated_at=1,
    )
    legacy = header.model_copy(update={'data': {'content': content}})

    async def get_header(_file_id):
        return header

    async def get_legacy(_file_id):
        return legacy

    monkeypatch.setattr(Files, 'get_file_by_id_without_content', get_header)
    monkeypatch.setattr(Files, 'get_file_by_id', get_legacy)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'query_string': b''})

    response = JSONCodec.loads(await view_file(
        'file-view', start_line=2, end_line=3, __request__=request, __user__={'id': 'owner-1', 'role': 'user'}
    ))

    assert response['content'] == '2: second line\n3: third line'
    assert response['revision'] == hashlib.sha256(content.encode('utf-8')).hexdigest()
    assert response['total_lines'] == 60_001
    assert response['evidence_id']

    bounded = JSONCodec.loads(await view_file(
        'file-view', start_line=1, end_line=4, max_chars=10,
        __request__=request, __user__={'id': 'owner-1', 'role': 'user'},
    ))
    assert len(bounded['content']) <= 10
    assert bounded['truncated'] is True
    assert bounded['next_start_line'] == 2
    async with factory() as db:
        migrated_file = await db.get(File, 'file-view')
    assert 'content' not in migrated_file.data
    assert storage.max_range <= store.chunk_bytes
    await engine.dispose()
