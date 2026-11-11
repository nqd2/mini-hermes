from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.agent_runtime.artifacts import AgentArtifactStore
from open_webui.agent_runtime.models import Base
from open_webui.agent_runtime.repository import AgentRepository


class LocalObjectStore:
    def __init__(self):
        self.values: dict[str, bytes] = {}

    async def put(self, key: str, content: bytes, mime_type: str):
        self.values[key] = content

    async def get_range(self, key: str, start: int, length: int) -> bytes:
        content = self.values[key]
        return content[start:start + length]

    async def delete(self, key: str):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_artifact_store_checks_owner_and_serves_byte_ranges(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "artifacts.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = AgentRepository(factory)
    storage = LocalObjectStore()
    artifacts = AgentArtifactStore(repository, storage)

    artifact = await artifacts.put('owner-a', 'tool output', b'x' * 5000, 'text/plain')
    section = await artifacts.read('owner-a', artifact.id, offset=100, length=32)

    assert section == b'x' * 32
    assert artifact.sha256 == hashlib.sha256(b'x' * 5000).hexdigest()
    with pytest.raises(HTTPException) as error:
        await artifacts.read('owner-b', artifact.id)
    assert error.value.status_code == 404
    assert len(storage.values[artifact.object_key]) == 5000

    unicode_text = 'α☃xyz'
    unicode_artifact = await artifacts.put('owner-a', 'unicode output', unicode_text.encode(), 'text/plain')
    pieces = []
    offset = 0
    while offset < unicode_artifact.size_bytes:
        piece, start, end, total = await artifacts.read_text_range(
            'owner-a', unicode_artifact.id, offset=offset, length=2
        )
        assert start == offset
        assert end > offset
        assert total == unicode_artifact.size_bytes
        pieces.append(piece)
        offset = end
    assert ''.join(pieces) == unicode_text
    await engine.dispose()
