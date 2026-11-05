"""HTTP client for the separately deployed document execution sandbox."""

from __future__ import annotations

import os

import httpx
from fastapi import HTTPException


class SandboxClient:
    def __init__(self):
        self.url = os.getenv('MH_SANDBOX_URL', 'http://127.0.0.1:8765').rstrip('/')

    async def _post(self, route: str, payload: dict) -> dict:
        token = os.getenv('MH_SANDBOX_TOKEN')
        if not token:
            raise HTTPException(503, 'MH_SANDBOX_TOKEN is not configured')
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(f'{self.url}{route}', json=payload,
                                             headers={'X-MH-Sandbox-Token': token})
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
        return await self._post('/run', payload)

    async def read(self, workspace_id: str, path: str) -> dict:
        return await self._post('/read', {'workspace_id': workspace_id, 'path': path})
