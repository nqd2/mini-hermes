"""HTTP client for the separately deployed document execution sandbox."""

from __future__ import annotations

import os

import httpx
from fastapi import HTTPException


class SandboxClient:
    def __init__(self):
        self.url = os.getenv('AGENT_RUNTIME_SANDBOX_URL', 'http://127.0.0.1:8765').rstrip('/')

    async def _post(self, route: str, payload: dict, *, timeout: int = 60) -> dict:
        token = os.getenv('AGENT_RUNTIME_SANDBOX_TOKEN')
        if not token:
            raise HTTPException(503, 'AGENT_RUNTIME_SANDBOX_TOKEN is not configured')
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f'{self.url}{route}', json=payload,
                                             headers={'X-Agent-Runtime-Sandbox-Token': token})
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get('detail', 'Sandbox rejected request')
            except ValueError:
                detail = 'Sandbox rejected request'
            raise HTTPException(exc.response.status_code, detail) from exc
        except httpx.RequestError as exc:
            raise HTTPException(503, 'Document sandbox is unavailable') from exc

    async def run(self, payload: dict) -> dict:
        return await self._post('/run', payload, timeout=max(60, payload.get('timeout_seconds', 30) + 15))

    async def sync(
        self,
        workspace_id: str,
        input_revisions: dict[str, int | str],
        inputs: dict[str, str] | None = None,
    ) -> dict:
        return await self._post(
            '/sync',
            {
                'workspace_id': workspace_id,
                'input_revisions': input_revisions,
                'inputs': inputs or {},
            },
            timeout=120,
        )

    async def read(self, workspace_id: str, path: str) -> dict:
        return await self._post('/read', {'workspace_id': workspace_id, 'path': path})

    async def fs_list(
        self, workspace_id: str, path: str = '.', *, max_depth: int = 3, limit: int = 200
    ) -> dict:
        return await self._post(
            '/fs/list',
            {'workspace_id': workspace_id, 'path': path, 'max_depth': max_depth, 'limit': limit},
        )

    async def fs_read(
        self,
        workspace_id: str,
        path: str,
        *,
        start_line: int = 1,
        end_line: int = 200,
        max_chars: int = 20_000,
        as_image: bool = False,
        raw_text: bool = False,
    ) -> dict:
        return await self._post(
            '/fs/read',
            {
                'workspace_id': workspace_id,
                'path': path,
                'start_line': start_line,
                'end_line': end_line,
                'max_chars': max_chars,
                'as_image': as_image,
                'raw_text': raw_text,
            },
        )

    async def fs_write(
        self, workspace_id: str, path: str, content: str, *, encoding: str = 'utf-8'
    ) -> dict:
        return await self._post(
            '/fs/write',
            {'workspace_id': workspace_id, 'path': path, 'content': content, 'encoding': encoding},
        )

    async def fs_edit(
        self,
        workspace_id: str,
        path: str,
        target: str,
        replacement: str,
        *,
        replace_all: bool = False,
    ) -> dict:
        return await self._post(
            '/fs/edit',
            {
                'workspace_id': workspace_id,
                'path': path,
                'target': target,
                'replacement': replacement,
                'replace_all': replace_all,
            },
        )

    async def fs_search(
        self,
        workspace_id: str,
        pattern: str,
        *,
        path: str = 'work',
        glob: str | None = None,
        case_insensitive: bool = False,
        max_matches: int = 50,
    ) -> dict:
        return await self._post(
            '/fs/search',
            {
                'workspace_id': workspace_id,
                'pattern': pattern,
                'path': path,
                'glob': glob,
                'case_insensitive': case_insensitive,
                'max_matches': max_matches,
            },
        )
