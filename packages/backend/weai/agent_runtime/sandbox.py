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

    async def read(self, workspace_id: str, path: str) -> dict:
        return await self._post('/read', {'workspace_id': workspace_id, 'path': path})
