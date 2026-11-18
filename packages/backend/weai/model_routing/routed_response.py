"""Expose the logical model group in OpenAI-compatible responses."""

from datetime import datetime, timezone

from fastapi import HTTPException

from weai.model_routing.tier_service import policy_error, upstream_free_reset

from weai.utils.json_codec import JSONCodec
from weai.utils.session_pool import stream_wrapper


def logical_result(value, group_id):
    if isinstance(value, list):
        return [logical_result(item, group_id) for item in value]
    if not isinstance(value, dict):
        return value
    public = {}
    for key, item in value.items():
        if key in {'provider', 'system_fingerprint', 'service_tier', 'native_finish_reason'}:
            continue
        if key == 'error':
            public[key] = {'message': 'Model request failed', 'code': 'provider_error'}
        elif key == 'model':
            public[key] = group_id
        else:
            public[key] = logical_result(item, group_id)
    return public


async def logical_stream(response, group_id):
    async for line in stream_wrapper(response):
        if line.startswith(b'data:'):
            body = line[5:].strip()
            if body == b'[DONE]':
                yield line
                continue
            try:
                data = JSONCodec.loads(body)
            except (ValueError, TypeError) as exc:
                raise ValueError('Invalid event from model provider') from exc
            yield f'data: {JSONCodec.dumps(logical_result(data, group_id))}\n'
        elif not line.strip() or line.startswith(b'event:'):
            yield line


def provider_error(response, selected):
    if selected.connection.is_openrouter and selected.candidate.model_id.endswith(':free'):
        reset = upstream_free_reset(response.status, response.headers, datetime.now(timezone.utc))
        if reset is not None:
            return policy_error('free_capacity_exhausted', 429, reset)
    retry_after = response.headers.get('Retry-After')
    headers = {'Retry-After': retry_after} if retry_after and retry_after.isdigit() else None
    return HTTPException(
        status_code=response.status if response.status >= 400 else 502,
        detail={
            'code': 'provider_rate_limit' if response.status == 429 else 'provider_error',
            'message': 'The model is busy. Try again shortly.' if response.status == 429 else 'Model request failed.',
        },
        headers=headers,
    )


def prepare_logical_request(gateway, payload, url, selected, overrides=None):
    if selected is None:
        return gateway.prepare(payload, url, overrides)
    try:
        return gateway.prepare(payload, url, selected.capability_overrides)
    except HTTPException as error:
        detail = error.detail
        if isinstance(detail, dict):
            detail = {
                key: value.replace(selected.candidate.model_id, selected.group_id) if isinstance(value, str) else value
                for key, value in detail.items()
            }
        raise HTTPException(error.status_code, detail=detail, headers=error.headers) from error
