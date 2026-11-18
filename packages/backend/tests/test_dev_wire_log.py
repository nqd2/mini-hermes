import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from weai.agent_runtime.gateway import ProviderGateway
from weai.utils.dev_wire_log import (
    DevJsonWireMiddleware,
    log_llm_json_response,
)
from weai.utils.session_pool import stream_wrapper
from starlette.responses import StreamingResponse


class _DummyStreamContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk

    async def iter_chunks(self):
        for chunk in self._chunks:
            yield chunk, True


class _DummyAiohttpResponse:
    def __init__(self, status, chunks):
        self.status = status
        self.content = _DummyStreamContent(chunks)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_dev_json_wire_middleware_logs_fe_be_and_redacts_secrets(monkeypatch, caplog, tmp_path):
    wire_file = tmp_path / 'wire.jsonl'
    monkeypatch.setenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG', '1')
    monkeypatch.setenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG_FILE', str(wire_file))

    app = FastAPI()
    app.add_middleware(DevJsonWireMiddleware)

    @app.post('/api/v1/auths/signin')
    async def signin(request: Request):
        body = await request.json()
        return {'email': body['email'], 'token': 'jwt-secret-value', 'usage': {'total_tokens': 42}}

    @app.post('/api/chat/completions')
    async def chat_stream():
        async def gen():
            yield b'data: {"choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n\n'
            yield b'data: [DONE]\n\n'

        return StreamingResponse(gen(), media_type='text/event-stream')

    caplog.set_level(logging.INFO, logger='weai.utils.dev_wire_log')
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        r1 = await client.post('/api/v1/auths/signin', json={'email': 'a@b.com', 'password': 'my-password'})
        assert r1.status_code == 200
        assert r1.json()['token'] == 'jwt-secret-value'

        r2 = await client.post('/api/chat/completions', json={'model': 'gpt-4o', 'messages': [{'role': 'user', 'content': 'hi'}]})
        assert r2.status_code == 200
        assert 'content' in r2.text

    messages = [rec.message for rec in caplog.records if '[DEV_JSON_WIRE]' in rec.message]
    assert any('[FE->BE] POST /api/v1/auths/signin' in m and '***REDACTED***' in m and 'my-password' not in m for m in messages)
    assert any('[BE->FE] status=200 POST /api/v1/auths/signin' in m and 'jwt-secret-value' not in m and '"total_tokens":42' in m for m in messages)
    assert any('[BE->FE:SSE] status=200 POST /api/chat/completions' in m and '"content":"hi"' in m for m in messages)

    records = [json.loads(line) for line in wire_file.read_text(encoding='utf-8').splitlines()]
    assert any(rec['tag'] == 'FE->BE' and rec['path'] == '/api/chat/completions' for rec in records)
    assert any(rec['tag'] == 'BE->FE:SSE' and '"content":"hi"' in rec['body'] for rec in records)


@pytest.mark.asyncio
async def test_dev_json_wire_middleware_noop_when_disabled(monkeypatch, caplog):
    monkeypatch.delenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG', raising=False)
    app = FastAPI()
    app.add_middleware(DevJsonWireMiddleware)

    @app.post('/api/test')
    async def endpoint(request: Request):
        return await request.json()

    caplog.set_level(logging.INFO, logger='weai.utils.dev_wire_log')
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        r = await client.post('/api/test', json={'ok': True})
        assert r.json() == {'ok': True}

    assert not any('[DEV_JSON_WIRE]' in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_provider_gateway_and_stream_wrapper_log_llm_json_and_sse(monkeypatch, caplog):
    monkeypatch.setenv('AGENT_RUNTIME_DEV_JSON_WIRE_LOG', '1')
    caplog.set_level(logging.INFO, logger='weai.utils.dev_wire_log')

    gateway = ProviderGateway()
    prepared = gateway.prepare(
        {'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'Read M.xlsx'}], 'stream': True},
        'https://api.example.com/v1',
    )

    sse_chunks = [
        b'data: {"id":"cmpl-1","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_","arguments":"{\\"file\\":"}}]}}]}\n\n',
        b'data: {"id":"cmpl-1","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"sheet","arguments":"\\"M.xlsx\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n',
        b'data: [DONE]\n\n',
    ]
    dummy_resp = _DummyAiohttpResponse(200, sse_chunks)

    class _DummySession:
        async def request(self, **kwargs):
            return dummy_resp

    resp = await gateway.send(
        _DummySession(),
        'POST',
        'https://api.example.com/v1/chat/completions',
        prepared,
    )

    collected = []
    async for chunk in stream_wrapper(resp, passthrough=True):
        collected.append(chunk)
    assert collected == sse_chunks

    non_stream_resp = SimpleNamespace(status=200)
    log_llm_json_response(non_stream_resp, 'https://api.example.com/v1/chat/completions', {'id': 'cmpl-2', 'choices': [{'message': {'content': 'done'}}]})

    messages = [rec.message for rec in caplog.records if '[DEV_JSON_WIRE]' in rec.message]
    assert any('[BE->LLM]' in m and 'https://api.example.com/v1/chat/completions' in m and 'Read M.xlsx' in m for m in messages)
    sse_logs = [m for m in messages if '[LLM->BE:SSE]' in m]
    assert len(sse_logs) == 1
    assert '"name":"read_sheet"' in sse_logs[0]
    assert '"arguments":"{\\"file\\":\\"M.xlsx\\"}"' in sse_logs[0]
    assert '"finish_reasons":["tool_calls"]' in sse_logs[0]
    assert any('[LLM->BE]' in m and '"id":"cmpl-2"' in m for m in messages)
