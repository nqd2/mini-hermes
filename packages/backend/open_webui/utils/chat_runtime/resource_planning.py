"""Resolve provider capabilities and initialize resource execution for a chat turn."""


async def prepare_resource_execution(request, metadata, prompt, user, model, config_get):
    """Select resource mode and snapshot model limits before evidence retrieval."""
    from open_webui.mini_hermes.resource_aware import resource_aware_context_enabled

    enabled = resource_aware_context_enabled(
        metadata.get('resource_history_files'),
        has_chat_resources=metadata.get('resource_chat_has_files', False),
    )
    metadata['resource_aware_files'] = enabled
    if not enabled:
        return None

    from open_webui.mini_hermes.resource_tasks import pending_tasks_for_chat

    pending_resource_task_ids = await pending_tasks_for_chat(metadata.get('chat_id'), user.id)
    from open_webui.mini_hermes.model_capabilities import resolve_model_capabilities

    provider_urls = await config_get('openai.api_base_urls') or []
    provider_index = model.get('urlIdx')
    if provider_index is None:
        provider_index = (
            (getattr(request.app.state, 'OPENAI_MODELS', {}) or {}).get(model.get('id'), {}).get('urlIdx')
        )
    provider_url = (
        provider_urls[provider_index]
        if isinstance(provider_index, int) and 0 <= provider_index < len(provider_urls)
        else ''
    )
    model_capabilities = resolve_model_capabilities(
        model.get('id', ''),
        provider_url,
        (model.get('info', {}).get('meta', {}).get('capabilities') or {}),
    )
    metadata['resource_model_tools'] = model_capabilities.supports_tools

    from open_webui.mini_hermes.execution.pipeline import ResourceRequestPipeline

    ResourceRequestPipeline().resolve(
        metadata,
        prompt or '',
        model_capabilities,
        pending_task_ids=pending_resource_task_ids,
    )
    request.state.resource_model_capabilities = {
        'tool_calling': model_capabilities.supports_tools,
        'parallel_tool_calls': model_capabilities.parallel_tool_calls,
        'context_window_tokens': model_capabilities.context_window_tokens,
        'max_output_tokens': model_capabilities.max_output_tokens,
    }
    return model_capabilities
