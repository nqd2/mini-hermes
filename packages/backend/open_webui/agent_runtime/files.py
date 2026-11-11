"""Secure, bounded, streaming filesystem retrieval."""

from __future__ import annotations

import asyncio
import base64
import codecs
import datetime as dt
import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select

from .models import AgentFileContent


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    size_bytes: int
    modified_ns: int
    line_count: int


@dataclass(frozen=True, slots=True)
class FileMatch:
    line_number: int
    byte_offset: int
    text: str


@dataclass(frozen=True, slots=True)
class FileContentReference:
    file_id: str
    owner_id: str
    object_key: str
    sha256: str
    size_bytes: int
    char_count: int
    line_count: int
    line_index: list[list[int]]
    char_index: list[list[int]]


class LargeFileReader:
    def __init__(
        self,
        allowed_roots: list[str | os.PathLike],
        *,
        chunk_size: int = 64 * 1024,
        max_range_bytes: int = 256 * 1024,
        max_line_bytes: int = 64 * 1024,
    ):
        if not allowed_roots:
            raise ValueError('at least one allowed root is required')
        self.allowed_roots = tuple(Path(root).resolve() for root in allowed_roots)
        self.chunk_size = max(1024, int(chunk_size))
        self.max_range_bytes = max(1, int(max_range_bytes))
        self.max_line_bytes = max(1024, int(max_line_bytes))
        self._metadata_cache: dict[str, tuple[int, int, FileMetadata]] = {}

    def _secure_path(self, path: str | os.PathLike) -> Path:
        resolved = Path(path).resolve(strict=True)
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise PermissionError('path is outside configured filesystem roots')
        if not resolved.is_file():
            raise ValueError('path is not a regular file')
        return resolved

    async def metadata(self, path: str | os.PathLike) -> FileMetadata:
        resolved = self._secure_path(path)
        stat = resolved.stat()
        key = str(resolved)
        cached = self._metadata_cache.get(key)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2]
        line_count = await asyncio.to_thread(self._count_lines, resolved)
        result = FileMetadata(key, stat.st_size, stat.st_mtime_ns, line_count)
        self._metadata_cache[key] = (stat.st_size, stat.st_mtime_ns, result)
        return result

    async def read_bytes(self, path: str | os.PathLike, offset: int, length: int) -> bytes:
        resolved = self._secure_path(path)
        if offset < 0 or length < 0 or length > self.max_range_bytes:
            raise ValueError(f'byte range must be between 0 and {self.max_range_bytes} bytes')
        return await asyncio.to_thread(self._read_bytes, resolved, offset, length)

    async def read_lines(
        self, path: str | os.PathLike, start_line: int, end_line: int, *, max_chars: int = 256_000
    ) -> list[tuple[int, str]]:
        resolved = self._secure_path(path)
        if start_line < 1 or end_line < start_line:
            raise ValueError('line range must be 1-based and inclusive')
        if end_line - start_line + 1 > 2000 or max_chars < 1:
            raise ValueError('line range exceeds configured limits')
        return await asyncio.to_thread(self._read_lines, resolved, start_line, end_line, max_chars)

    async def search(self, path: str | os.PathLike, query: str, *, max_matches: int = 100) -> list[FileMatch]:
        resolved = self._secure_path(path)
        if not query:
            raise ValueError('search query cannot be empty')
        if max_matches < 1 or max_matches > 1000:
            raise ValueError('max_matches must be between 1 and 1000')
        return await asyncio.to_thread(self._search, resolved, query, max_matches)

    def iter_files(self, root: str | os.PathLike) -> Iterator[Path]:
        """Yield files with O(directory depth) memory; no repository-wide list is built."""
        resolved_root = Path(root).resolve(strict=True)
        if not any(resolved_root == allowed or allowed in resolved_root.parents for allowed in self.allowed_roots):
            raise PermissionError('path is outside configured filesystem roots')
        stack = [resolved_root]
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path)
                    except OSError:
                        continue

    def invalidate(self, path: str | os.PathLike) -> None:
        self._metadata_cache.pop(str(Path(path).resolve()), None)

    def _count_lines(self, path: Path) -> int:
        count = 0
        last_byte = b''
        with path.open('rb') as stream:
            while chunk := stream.read(self.chunk_size):
                count += chunk.count(b'\n')
                last_byte = chunk[-1:]
        if not last_byte:
            return 0
        return count + (last_byte != b'\n')

    @staticmethod
    def _read_bytes(path: Path, offset: int, length: int) -> bytes:
        with path.open('rb') as stream:
            stream.seek(offset)
            return stream.read(length)

    def _read_lines(self, path: Path, start: int, end: int, max_chars: int) -> list[tuple[int, str]]:
        result = []
        size = 0
        with path.open('rb') as stream:
            line_number = 0
            while True:
                raw = stream.readline(self.max_line_bytes + 1)
                if not raw:
                    break
                line_number += 1
                if line_number < start:
                    while raw and not raw.endswith(b'\n'):
                        raw = stream.readline(self.max_line_bytes + 1)
                    continue
                if line_number > end:
                    break
                is_partial = len(raw) > self.max_line_bytes and not raw.endswith(b'\n')
                raw = raw[: self.max_line_bytes]
                text = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                if is_partial:
                    text += ' …[line truncated]'
                if size + len(text) > max_chars:
                    text = text[: max_chars - size]
                    if not text:
                        break
                result.append((line_number, text))
                size += len(text)
                if size >= max_chars:
                    break
                if is_partial:
                    while raw and not raw.endswith(b'\n'):
                        raw = stream.readline(self.max_line_bytes + 1)
        return result

    def _search(self, path: Path, query: str, max_matches: int) -> list[FileMatch]:
        result = []
        carry = b''
        byte_offset = 0
        line_number = 1
        query_bytes = query.encode('utf-8')
        overlap = max(0, len(query_bytes) - 1)
        with path.open('rb') as stream:
            while chunk := stream.read(self.chunk_size):
                window = carry + chunk
                base_offset = byte_offset - len(carry)
                base_line = line_number - carry.count(b'\n')
                start = 0
                while len(result) < max_matches:
                    index = window.find(query_bytes, start)
                    if index < 0:
                        break
                    absolute = base_offset + index
                    # Carry-over matches were reported in the prior chunk.
                    if absolute >= byte_offset:
                        before = window[:index]
                        line = base_line + before.count(b'\n')
                        line_start = window.rfind(b'\n', 0, index) + 1
                        line_end = window.find(b'\n', index)
                        if line_end < 0:
                            line_end = len(window)
                        excerpt = window[line_start:line_end][: self.max_line_bytes]
                        result.append(FileMatch(line, absolute, excerpt.decode('utf-8', errors='replace')))
                    start = index + 1
                byte_offset += len(chunk)
                line_number += chunk.count(b'\n')
                carry = window[-overlap:] if overlap else b''
                if len(result) >= max_matches:
                    break
        return result


class AgentFileContentStore:
    """Move extracted file text to verified object storage and seek it in bounded ranges."""

    LINE_INDEX_STRIDE = 128
    CHAR_INDEX_STRIDE = 4096

    def __init__(
        self,
        session_factory,
        object_store,
        *,
        inline_threshold_bytes: int = 256 * 1024,
        chunk_bytes: int = 64 * 1024,
        max_line_chars: int = 64 * 1024,
    ):
        self.session_factory = session_factory
        self.object_store = object_store
        self.inline_threshold_bytes = max(1, inline_threshold_bytes)
        self.chunk_bytes = max(1024, chunk_bytes)
        self.max_line_chars = max(1024, max_line_chars)

    @classmethod
    def configured(cls):
        from open_webui.internal.db import AsyncSessionLocal
        from open_webui.mini_hermes.storage import ObjectStore

        return cls(AsyncSessionLocal, ObjectStore())

    @staticmethod
    def _reference(row: AgentFileContent) -> FileContentReference:
        return FileContentReference(
            file_id=row.file_id,
            owner_id=row.owner_id,
            object_key=row.object_key,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            char_count=row.char_count,
            line_count=row.line_count,
            line_index=row.line_index,
            char_index=row.char_index,
        )

    async def get_reference(self, file_id: str, *, owner_id: str) -> FileContentReference | None:
        async with self.session_factory() as db:
            row = await db.get(AgentFileContent, file_id)
            if row is None:
                return None
            if row.owner_id != owner_id:
                raise HTTPException(status_code=404, detail='File not found.')
            return self._reference(row)

    async def migrate_file(self, file_id: str, *, owner_id: str) -> FileContentReference | None:
        """Copy, checksum-verify, then replace the legacy JSON body with an object reference."""
        from open_webui.models.files import File

        async with self.session_factory() as db:
            async with db.begin():
                file = await db.scalar(select(File).where(File.id == file_id).with_for_update())
                if file is None or file.user_id != owner_id:
                    raise HTTPException(status_code=404, detail='File not found.')
                existing = await db.get(AgentFileContent, file_id)
                if existing is not None:
                    return self._reference(existing)
                data = dict(file.data) if isinstance(file.data, dict) else {}
                content = data.get('content')
                if not isinstance(content, str):
                    return None

        raw = content.encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        owner_component = base64.urlsafe_b64encode(owner_id.encode('utf-8')).decode('ascii').rstrip('=')
        file_component = base64.urlsafe_b64encode(file_id.encode('utf-8')).decode('ascii').rstrip('=')
        object_key = f'agent-files/{owner_component}/{file_component}/{digest}.txt'
        line_index, char_index, line_count = _build_text_indexes(content)
        await self.object_store.put(object_key, raw, 'text/plain; charset=utf-8')
        try:
            verifier = hashlib.sha256()
            verified_size = 0
            while verified_size < len(raw):
                chunk = await self.object_store.get_range(
                    object_key,
                    verified_size,
                    min(self.chunk_bytes, len(raw) - verified_size),
                )
                if not chunk:
                    raise OSError('object store returned a short file during checksum verification')
                verifier.update(chunk)
                verified_size += len(chunk)
            if verified_size != len(raw) or verifier.hexdigest() != digest:
                raise OSError('object-store checksum verification failed')

            async with self.session_factory() as db:
                async with db.begin():
                    file = await db.scalar(select(File).where(File.id == file_id).with_for_update())
                    if file is None or file.user_id != owner_id:
                        raise HTTPException(status_code=404, detail='File not found.')
                    existing = await db.get(AgentFileContent, file_id)
                    if existing is not None:
                        if existing.sha256 != digest:
                            raise RuntimeError('file content changed during migration')
                        return self._reference(existing)
                    current_data = dict(file.data) if isinstance(file.data, dict) else {}
                    current_content = current_data.get('content')
                    if (
                        not isinstance(current_content, str)
                        or hashlib.sha256(current_content.encode('utf-8')).hexdigest() != digest
                    ):
                        raise RuntimeError('file content changed during migration')
                    row = AgentFileContent(
                        file_id=file_id,
                        owner_id=owner_id,
                        object_key=object_key,
                        sha256=digest,
                        size_bytes=len(raw),
                        char_count=len(content),
                        line_count=line_count,
                        line_index=line_index,
                        char_index=char_index,
                        created_at=dt.datetime.now(dt.UTC),
                    )
                    db.add(row)
                    from open_webui.config import RAG_FILE_CONTENT_SEARCH_MAX_CHARS

                    current_data['content_search'] = current_content[:RAG_FILE_CONTENT_SEARCH_MAX_CHARS]
                    current_data.pop('content', None)
                    file.data = current_data
                    file.updated_at = int(dt.datetime.now(dt.UTC).timestamp())
                return self._reference(row)
        except BaseException:
            # Do not delete a deterministic key another concurrent migration committed.
            async with self.session_factory() as db:
                committed = await db.get(AgentFileContent, file_id)
            if committed is None or committed.object_key != object_key:
                await self.object_store.delete(object_key)
            raise

    async def read_chars(self, reference: FileContentReference, offset: int, length: int) -> tuple[str, int, int]:
        if offset < 0 or length < 0:
            raise ValueError('character range must be non-negative')
        offset = min(offset, reference.char_count)
        length = min(length, reference.char_count - offset)
        anchor = _nearest_index(reference.char_index, offset)
        anchor_char, byte_offset, anchor_line = anchor
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        byte_cursor = byte_offset
        char_cursor = anchor_char
        line_at_offset = anchor_line
        result = []
        remaining = length
        while byte_cursor < reference.size_bytes and remaining:
            raw = await self.object_store.get_range(
                reference.object_key, byte_cursor, min(self.chunk_bytes, reference.size_bytes - byte_cursor)
            )
            if not raw:
                break
            byte_cursor += len(raw)
            decoded = decoder.decode(raw)
            skip = min(max(0, offset - char_cursor), len(decoded))
            if skip:
                line_at_offset += decoded[:skip].count('\n')
            segment = decoded[skip:]
            if segment:
                take = segment[:remaining]
                result.append(take)
                remaining -= len(take)
                char_cursor += len(decoded)
            elif decoded:
                char_cursor += len(decoded)
        if remaining and byte_cursor >= reference.size_bytes:
            decoded = decoder.decode(b'', final=True)
            skip = min(max(0, offset - char_cursor), len(decoded))
            if skip:
                line_at_offset += decoded[:skip].count('\n')
            result.append(decoded[skip : skip + remaining])
        return ''.join(result), reference.char_count, line_at_offset

    async def read_lines(
        self, reference: FileContentReference, start_line: int, end_line: int, *, max_chars: int = 256_000
    ) -> list[tuple[int, str, int, int]]:
        if start_line < 1 or end_line < start_line:
            raise ValueError('line range must be 1-based and inclusive')
        if end_line - start_line + 1 > 2000 or max_chars < 1:
            raise ValueError('line range exceeds configured limits')
        if start_line > reference.line_count:
            return []
        line_anchor = _nearest_line_index(reference.line_index, start_line)
        line_number, byte_cursor, line_start = line_anchor
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        line_text = ''
        line_length = 0
        line_truncated = False
        output_chars = 0
        result: list[tuple[int, str, int, int]] = []

        def consume(decoded: str) -> bool:
            nonlocal line_number, line_start, line_text, line_length, line_truncated, output_chars
            parts = decoded.split('\n')
            for part_index, part in enumerate(parts):
                if line_number >= start_line and not line_truncated:
                    room = max(0, self.max_line_chars - len(line_text))
                    line_text += part[:room]
                    if len(part) > room:
                        line_truncated = True
                line_length += len(part)
                if part_index < len(parts) - 1:
                    if start_line <= line_number <= end_line:
                        displayed = line_text + (' …[line truncated]' if line_truncated else '')
                        remaining = max_chars - output_chars
                        if remaining <= 0:
                            return True
                        if len(displayed) > remaining:
                            displayed = displayed[:remaining]
                        result.append(
                            (line_number, displayed, line_start, line_start + min(line_length, len(displayed)))
                        )
                        output_chars += len(displayed)
                        if output_chars >= max_chars or line_number >= end_line:
                            return True
                    line_start += line_length + 1
                    line_number += 1
                    line_text = ''
                    line_length = 0
                    line_truncated = False
                elif line_number >= start_line and output_chars < max_chars:
                    continue
            return False

        while byte_cursor < reference.size_bytes:
            raw = await self.object_store.get_range(
                reference.object_key, byte_cursor, min(self.chunk_bytes, reference.size_bytes - byte_cursor)
            )
            if not raw:
                break
            byte_cursor += len(raw)
            if consume(decoder.decode(raw)):
                return result
        trailing = decoder.decode(b'', final=True)
        if trailing and consume(trailing):
            return result
        if start_line <= line_number <= end_line and line_number <= reference.line_count and output_chars < max_chars:
            displayed = line_text + (' …[line truncated]' if line_truncated else '')
            remaining = max_chars - output_chars
            if len(displayed) > remaining:
                displayed = displayed[:remaining]
            result.append((line_number, displayed, line_start, line_start + min(line_length, len(displayed))))
        return result

    async def read_all(self, reference: FileContentReference) -> str:
        chunks = []
        offset = 0
        while offset < reference.size_bytes:
            chunk = await self.object_store.get_range(
                reference.object_key, offset, min(self.chunk_bytes, reference.size_bytes - offset)
            )
            if not chunk:
                raise OSError('object store returned a short file')
            chunks.append(chunk)
            offset += len(chunk)
        raw = b''.join(chunks)
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise OSError('stored file content failed checksum verification')
        return raw.decode('utf-8')

    async def migrate_legacy_files(self, *, batch_size: int = 50, max_files: int | None = None) -> int:
        """Migrate legacy extracted bodies in bounded batches; intended for an operator-run job."""
        from open_webui.models.files import File

        if batch_size < 1 or batch_size > 500:
            raise ValueError('batch_size must be between 1 and 500')
        migrated = 0
        cursor = ''
        while max_files is None or migrated < max_files:
            async with self.session_factory() as db:
                rows = await db.execute(
                    select(File.id, File.user_id)
                    .where(File.id > cursor, File.data['content'].as_string().is_not(None))
                    .order_by(File.id)
                    .limit(min(batch_size, max_files - migrated) if max_files is not None else batch_size)
                )
                candidates = list(rows.all())
            if not candidates:
                break
            for file_id, owner_id in candidates:
                await self.migrate_file(file_id, owner_id=owner_id)
                migrated += 1
            cursor = candidates[-1].id
        return migrated

    async def count_legacy_files(self) -> int:
        from open_webui.models.files import File

        async with self.session_factory() as db:
            return int(
                await db.scalar(
                    select(func.count()).select_from(File).where(File.data['content'].as_string().is_not(None))
                )
                or 0
            )


def _build_text_indexes(content: str) -> tuple[list[list[int]], list[list[int]], int]:
    line_index = [[1, 0, 0]]
    char_index = [[0, 0, 1]]
    line_number = 1
    byte_offset = 0
    char_count = 0
    for char in content:
        if char_count and char_count % AgentFileContentStore.CHAR_INDEX_STRIDE == 0:
            char_index.append([char_count, byte_offset, line_number])
        byte_offset += len(char.encode('utf-8'))
        char_count += 1
        if char == '\n':
            line_number += 1
            if (line_number - 1) % AgentFileContentStore.LINE_INDEX_STRIDE == 0:
                line_index.append([line_number, byte_offset, char_count])
    return line_index, char_index, line_number


def _nearest_index(index: list[list[int]], offset: int) -> tuple[int, int, int]:
    candidate = index[0]
    for item in index[1:]:
        if item[0] > offset:
            break
        candidate = item
    return int(candidate[0]), int(candidate[1]), int(candidate[2])


def _nearest_line_index(index: list[list[int]], line: int) -> tuple[int, int, int]:
    candidate = index[0]
    for item in index[1:]:
        if item[0] > line:
            break
        candidate = item
    return int(candidate[0]), int(candidate[1]), int(candidate[2])
