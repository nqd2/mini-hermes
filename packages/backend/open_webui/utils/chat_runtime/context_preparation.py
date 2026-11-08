"""Assemble request-scoped prompt context and citation events."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextPreparationAdapters:
    """Existing prompt and citation functions supplied by the chat middleware."""

    apply_source_context_to_messages: Callable[..., Any]
    build_resource_instructions: Callable[..., str | None]
    resolve_system_prompt: Callable[..., Any]
    get_system_message: Callable[..., dict | None]
    get_content_from_message: Callable[..., str | None]
    get_last_user_message: Callable[..., str | None]


async def prepare_chat_context(
    request,
    form_data,
    metadata,
    events,
    sources,
    prompt,
    model_system_prompt_template,
    user,
    adapters: ContextPreparationAdapters,
):
    """Restore the base prompts, inject current evidence, and emit citation sources."""
    system_message = adapters.get_system_message(form_data['messages'])
    base_system_prompt = adapters.get_content_from_message(system_message) if system_message else ''
    resource_instructions = adapters.build_resource_instructions(metadata)
    if resource_instructions:
        base_system_prompt = f'{base_system_prompt}\n\n{resource_instructions}'.strip()

    resolved_model_system_prompt = await adapters.resolve_system_prompt(model_system_prompt_template, metadata, user)
    if resolved_model_system_prompt:
        base_system_prompt = (
            f'{resolved_model_system_prompt}\n{base_system_prompt}'
            if base_system_prompt
            else resolved_model_system_prompt
        )

    metadata['base_system_prompt'] = base_system_prompt or None
    metadata['base_user_prompt'] = adapters.get_last_user_message(form_data['messages'])
    metadata['sources'] = sources[:] if sources else []

    if sources and prompt:
        form_data['messages'] = await adapters.apply_source_context_to_messages(
            request,
            form_data['messages'],
            sources,
            prompt,
        )

    citation_sources = [
        source
        for source in sources
        if source.get('source', {}).get('name', '') or source.get('source', {}).get('id', '')
    ]
    if citation_sources:
        events.append({'sources': citation_sources})

    return form_data, metadata, events
