"""Owner-checked durable artifacts stored in the configured S3-compatible object store."""

from __future__ import annotations

import codecs
import hashlib
import secrets
from typing import Any

from fastapi import HTTPException

from .models import AgentArtifact, utcnow
from .repository import AgentRepository


class AgentArtifactStore:
    def __init__(self, repository: AgentRepository, object_store: Any, *, max_read_bytes: int = 256 * 1024):
        self.repository = repository
        self.object_store = object_store
        self.max_read_bytes = max_read_bytes

    @classmethod
    def configured(cls, *, max_read_bytes: int = 256 * 1024) -> AgentArtifactStore:
        from open_webui.internal.db import AsyncSessionLocal
        from open_webui.mini_hermes.storage import ObjectStore

        return cls(AgentRepository(AsyncSessionLocal), ObjectStore(), max_read_bytes=max_read_bytes)

    async def put(
        self,
        owner_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        metadata: dict | None = None,
    ) -> AgentArtifact:
        if not owner_id:
            raise ValueError('owner_id is required')
        if not isinstance(content, bytes):
            raise TypeError('artifact content must be bytes')
        artifact_id = secrets.token_urlsafe(16)
        object_key = f'agent-artifacts/{owner_id}/{artifact_id}'
        digest = hashlib.sha256(content).hexdigest()
        await self.object_store.put(object_key, content, mime_type)
        artifact = AgentArtifact(
            id=artifact_id,
            owner_id=owner_id,
            session_id=session_id,
            turn_id=turn_id,
            object_key=object_key,
            sha256=digest,
            size_bytes=len(content),
            mime_type=mime_type,
            filename=filename,
            metadata_json=metadata,
            created_at=utcnow(),
        )
        try:
            return await self.repository.record_artifact(artifact)
        except BaseException:
            await self.object_store.delete(object_key)
            raise

    async def read(self, owner_id: str, artifact_id: str, *, offset: int = 0, length: int | None = None) -> bytes:
        artifact = await self.repository.get_artifact(artifact_id, owner_id=owner_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail='Artifact not found.')
        if offset < 0 or offset > artifact.size_bytes:
            raise HTTPException(status_code=416, detail='Artifact byte offset is outside the object.')
        read_length = self.max_read_bytes if length is None else int(length)
        if read_length < 0 or read_length > self.max_read_bytes:
            raise HTTPException(status_code=416, detail='Artifact reads are limited to 256 KiB.')
        read_length = min(read_length, artifact.size_bytes - offset)
        if hasattr(self.object_store, 'get_range'):
            return await self.object_store.get_range(artifact.object_key, offset, read_length)
        content = await self.object_store.get(artifact.object_key)
        return content[offset : offset + read_length]

    async def read_text_range(
        self, owner_id: str, artifact_id: str, *, offset: int = 0, length: int = 24_000
    ) -> tuple[str, int, int, int]:
        """Read a UTF-8-safe byte range, preserving exact continuation offsets."""
        artifact = await self.repository.get_artifact(artifact_id, owner_id=owner_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail='Artifact not found.')
        if offset < 0 or offset > artifact.size_bytes:
            raise HTTPException(status_code=416, detail='Artifact byte offset is outside the object.')
        if length < 1:
            raise HTTPException(status_code=416, detail='Artifact range length must be positive.')
        start = offset
        max_bytes = max(1, self.max_read_bytes - 3)
        requested = min(int(length), max_bytes, artifact.size_bytes - start)
        raw = await self.read(
            owner_id,
            artifact_id,
            offset=start,
            length=min(max_bytes, requested + 3),
        )
        while raw and raw[0] & 0xC0 == 0x80:
            raw = raw[1:]
            start += 1
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        text = decoder.decode(raw, final=False)
        incomplete = decoder.getstate()[0]
        complete_bytes = len(raw) - len(incomplete)
        complete_text = raw[:complete_bytes].decode('utf-8', errors='strict')
        selected = []
        consumed_bytes = 0
        for char in complete_text:
            char_bytes = len(char.encode('utf-8'))
            if selected and consumed_bytes + char_bytes > requested:
                break
            selected.append(char)
            consumed_bytes += char_bytes
            if consumed_bytes >= requested:
                break
        text = ''.join(selected)
        end = start + consumed_bytes
        return text, start, end, artifact.size_bytes
