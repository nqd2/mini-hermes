"""Single validated gateway for outbound model JSON requests."""

from __future__ import annotations

import logging
from typing import Any

from open_webui.utils.json_codec import JSONCodec

from .context import ContextManager, PreparedContext
from .contracts import compute_run_snapshot_versions
from .request_budget import RequestBudgetManager

log = logging.getLogger(__name__)


class ProviderGateway:
    def __init__(
        self,
        *,
        context_manager: ContextManager | None = None,
        request_budget: RequestBudgetManager | None = None,
    ):
        if context_manager and request_budget:
            raise ValueError('pass context_manager or request_budget, not both')
        self.context_manager = context_manager or ContextManager(request_budget=request_budget)

    def prepare(
        self,
        payload: dict,
        base_url: str,
        capability_overrides: dict | None = None,
    ) -> PreparedContext:
        prepared = self.context_manager.prepare(payload, base_url, capability_overrides)
        from open_webui.mini_hermes.provider_guard import validate_provider_payload

        provider_metrics = validate_provider_payload(
            prepared.wire_body,
            prepared.payload,
            base_url,
            capability_overrides,
        )
        prepared = PreparedContext(
            payload=prepared.payload,
            wire_body=prepared.wire_body,
            metrics={**prepared.metrics, **provider_metrics},
        )
        log.info(
            'Prepared provider request: model=%s chars=%d bytes=%d tokens=%d compacted_messages=%d',
            prepared.payload.get('model'),
            prepared.metrics['serialized_chars'],
            prepared.metrics['serialized_bytes'],
            prepared.metrics['estimated_input_tokens'],
            prepared.metrics['compacted_messages'],
        )
        return prepared

    async def checkpoint(self, request: Any, prepared: PreparedContext) -> None:
        """Persist context reconstruction data before an upstream request begins."""
        metadata = getattr(getattr(request, 'state', None), 'metadata', None) or {}
        turn_id = metadata.get('agent_turn_id')
        if not turn_id:
            return
        from .runtime import AgentRuntime

        runtime = AgentRuntime.configured()
        tool_contract_version, skill_snapshot_version = compute_run_snapshot_versions(prepared.payload)
        run_binding = await runtime.repository.bind_run_contract(
            turn_id,
            tool_contract_version=tool_contract_version,
            skill_snapshot_version=skill_snapshot_version,
        )
        metadata['agent_run_binding'] = run_binding.as_dict()
        await runtime.repository.save_checkpoint(
            turn_id,
            {
                'schema_version': 1,
                'stage': 'provider_request',
                'model': prepared.payload.get('model'),
                'run_binding': run_binding.as_dict(),
                'payload': prepared.payload,
                'metrics': prepared.metrics,
            },
            kind='context',
        )
        if prepared.metrics.get('compacted_messages'):
            await runtime.repository.append_event(
                turn_id,
                'context.compacted',
                {
                    'removed_messages': prepared.metrics['compacted_messages'],
                    'estimated_input_tokens': prepared.metrics['estimated_input_tokens'],
                    'serialized_chars': prepared.metrics['serialized_chars'],
                    'serialized_bytes': prepared.metrics['serialized_bytes'],
                },
            )

    async def send(
        self,
        session: Any,
        method: str,
        url: str,
        prepared: PreparedContext,
        **request_options: Any,
    ) -> Any:
        if not isinstance(prepared, PreparedContext):
            raise TypeError('ProviderGateway.send requires a prepared and validated request')
        payload = JSONCodec.loads(prepared.wire_body)
        if not isinstance(payload, dict):
            raise ValueError('prepared model body is not a JSON object')
        # Recheck the exact hard transport limits immediately before sending.
        # No serializer or provider transform runs between this check and HTTP.
        metrics = self.context_manager.request_budget.validate(prepared.wire_body)
        request_options.pop('provider_base_url', None)
        request_options.pop('capability_overrides', None)
        body_argument = request_options.pop('body_argument', 'data')
        if body_argument in request_options:
            raise ValueError(f'{body_argument} is reserved for the prepared request body')
        log.info(
            'Sending provider request: model=%s chars=%d bytes=%d tokens=%d',
            payload.get('model'),
            metrics['serialized_chars'],
            metrics['serialized_bytes'],
            prepared.metrics['estimated_input_tokens'],
        )
        return await session.request(method=method, url=url, **{body_argument: prepared.wire_body}, **request_options)
