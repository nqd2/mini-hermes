"""Resolve, load, and present chat skills without coupling request orchestration to storage."""

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import HTTPException


@dataclass
class ChatSkillServices:
    """Lazy-loaded adapters for database and terminal-backed skill resources."""

    get_db_skills: Callable
    get_skill_snapshot: Callable
    skill_overview: Callable
    format_skill_content: Callable
    get_terminal_json: Callable
    get_terminal_skill: Callable
    format_terminal_skill_context: Callable
    format_terminal_skill_manifest_entry: Callable


@dataclass
class ChatSkillContextAdapters:
    """Open WebUI helpers used to update messages and resolve skill mentions."""

    config_get: Callable
    extract_skill_ids_from_messages: Callable
    strip_skill_mentions: Callable
    add_or_update_system_message: Callable
    get_last_user_message: Callable
    set_last_user_message_content: Callable
    load_services: Callable[[], ChatSkillServices]


@dataclass
class ChatSkillContext:
    skill_ids: list[str]
    available_skills: list
    terminal_skills: list
    resolved_skill_ids: set[str]
    model_builtin_tools: dict
    prompt: str


def load_chat_skill_services() -> ChatSkillServices:
    """Load skill storage/formatting dependencies only when this request has skills."""
    from open_webui.models.skills import Skills as SkillsModel
    from open_webui.models.skills import get_skill_snapshot
    from open_webui.utils.skill_files import format_skill_content, skill_overview
    from open_webui.utils.terminals import (
        format_terminal_skill_context,
        format_terminal_skill_manifest_entry,
        get_terminal_json,
        get_terminal_skill,
    )

    return ChatSkillServices(
        get_db_skills=SkillsModel.get_skills,
        get_skill_snapshot=get_skill_snapshot,
        skill_overview=skill_overview,
        format_skill_content=format_skill_content,
        get_terminal_json=get_terminal_json,
        get_terminal_skill=get_terminal_skill,
        format_terminal_skill_context=format_terminal_skill_context,
        format_terminal_skill_manifest_entry=format_terminal_skill_manifest_entry,
    )


async def prepare_chat_skill_context(
    request,
    form_data,
    user,
    model,
    metadata,
    extra_params,
    *,
    use_builtin_tools: bool,
    payload_tools,
    terminal_id,
    adapters: ChatSkillContextAdapters,
) -> ChatSkillContext:
    """Resolve requested skills and add their bounded instructions or manifests."""
    chat_context = metadata.get('chat_context') or {}
    metadata['chat_context'] = chat_context
    skill_versions = chat_context.setdefault('skill_versions', {})
    mentioned_skill_ids = set(chat_context.get('mentioned_skill_ids') or []) | adapters.extract_skill_ids_from_messages(
        form_data.get('messages', [])
    )
    activated_skill_ids = set(form_data.pop('skill_ids', None) or []) | mentioned_skill_ids
    skill_ids = sorted(activated_skill_ids | set(model.get('info', {}).get('meta', {}).get('skillIds', [])))
    if payload_tools is None and await adapters.config_get('skills.lazy_loading.enable', False):
        activated_skill_ids = mentioned_skill_ids

    model_builtin_tools = model.get('info', {}).get('meta', {}).get('builtinTools') or {}
    available_skills = []
    terminal_skills = []

    if skill_ids or (use_builtin_tools and model_builtin_tools.get('skills', True)):
        services = adapters.load_services()
        terminal_skill_prefix = 'terminal:'
        db_skill_ids = [sid for sid in skill_ids if not sid.startswith(terminal_skill_prefix)]
        terminal_skill_ids = [sid for sid in skill_ids if sid.startswith(terminal_skill_prefix)]
        available_skills, skill_manifest = await _prepare_database_skill_context(
            services,
            db_skill_ids,
            activated_skill_ids,
            skill_versions,
            user,
            use_builtin_tools,
            model_builtin_tools,
            form_data,
            adapters,
        )
        terminal_skills, terminal_manifest = await _prepare_terminal_skill_context(
            services,
            terminal_skill_ids,
            activated_skill_ids,
            request,
            user,
            metadata,
            extra_params,
            terminal_id,
            use_builtin_tools,
            model_builtin_tools,
            form_data,
            adapters,
        )
        skill_manifest += terminal_manifest
        if skill_manifest:
            form_data['messages'] = adapters.add_or_update_system_message(
                f'<available_skills>\n{skill_manifest}</available_skills>',
                form_data['messages'],
                append=True,
            )

    resolved_skill_ids = {s.id for s in available_skills} | {s['id'] for s in terminal_skills}
    chat_context['mentioned_skill_ids'] = sorted(mentioned_skill_ids & resolved_skill_ids)
    adapters.strip_skill_mentions(form_data.get('messages', []), resolved_skill_ids)

    if metadata.get('mh_agent_enabled') and use_builtin_tools:
        form_data['messages'] = adapters.add_or_update_system_message(
            'Mini-Hermes document agent is active. Available bundled skills: '
            'docx, pdf, pptx, xlsx. Read the relevant SKILL.md with '
            'read_document_skill before using its scripts. Work only with '
            'documents granted to this chat. Use run_document_code or '
            'run_skill_script to prepare and inspect files. publish_document_output '
            'creates a staged proposal for review; it does not save a changed revision. '
            'Never claim that a file changed or was saved until the approved commit succeeds.',
            form_data['messages'],
            append=True,
        )

    prompt = adapters.get_last_user_message(form_data['messages'])
    if not prompt or not prompt.strip():
        fallback = ', '.join([s.name for s in available_skills] + [s['name'] for s in terminal_skills])
        if fallback and not (metadata.get('user_message') or {}).get('files'):
            adapters.set_last_user_message_content(fallback, form_data['messages'])
            prompt = fallback

    return ChatSkillContext(
        skill_ids=skill_ids,
        available_skills=available_skills,
        terminal_skills=terminal_skills,
        resolved_skill_ids=resolved_skill_ids,
        model_builtin_tools=model_builtin_tools,
        prompt=prompt,
    )


async def _prepare_database_skill_context(
    services,
    skill_ids,
    activated_skill_ids,
    skill_versions,
    user,
    use_builtin_tools,
    model_builtin_tools,
    form_data,
    adapters,
):
    skill_tools_enabled = use_builtin_tools and model_builtin_tools.get('skills', True)
    if skill_tools_enabled:
        accessible_skills = {skill.id: skill for skill in await services.get_db_skills(user_id=user.id)}
        skill_ids = sorted(accessible_skills)
    else:
        accessible_skills = {skill.id: skill for skill in await services.get_db_skills(user_id=user.id, ids=skill_ids)}

    available_skills = [
        accessible_skills[sid] for sid in skill_ids if sid in accessible_skills and accessible_skills[sid].is_active
    ]
    manifest = []
    for skill in available_skills:
        if skill.id in activated_skill_ids or not skill_tools_enabled:
            await _append_database_skill_instructions(
                services, skill, skill_versions, skill_tools_enabled, form_data, adapters
            )
        else:
            manifest.append(
                f'<skill>\n<id>{skill.id}</id>\n<name>{skill.name}</name>\n'
                f'<description>{skill.description or ""}</description>\n</skill>\n'
            )
    return available_skills, ''.join(manifest)


async def _append_database_skill_instructions(services, skill, skill_versions, tools_enabled, form_data, adapters):
    version_id = skill_versions.get(skill.id) or skill.version_id
    try:
        snapshot = await services.get_skill_snapshot(skill, version_id)
    except HTTPException as error:
        if error.status_code == 404:
            return
        raise

    skill_versions[skill.id] = version_id
    overview = await services.skill_overview(
        snapshot['content'],
        [file['path'] for file in snapshot['data']['files'] if file['path'] != 'SKILL.md'],
        tools_enabled=tools_enabled,
    )
    root_content = services.format_skill_content(overview, skill.id, tools_enabled)
    resources = '\n'.join(overview['files'])
    resource_hint = (
        f'\nSupporting files:\n{resources}'
        if tools_enabled and resources
        else '\nSupporting files require a model with skill tools enabled.'
        if resources
        else ''
    )
    if overview.get('notice'):
        resource_hint += '\n' + overview['notice']
    form_data['messages'] = adapters.add_or_update_system_message(
        f'<skill id="{skill.id}" name="{skill.name}">\n{root_content}{resource_hint}\n</skill>',
        form_data['messages'],
        append=True,
    )


async def _prepare_terminal_skill_context(
    services,
    terminal_skill_ids,
    activated_skill_ids,
    request,
    user,
    metadata,
    extra_params,
    terminal_id,
    use_builtin_tools,
    model_builtin_tools,
    form_data,
    adapters,
):
    skill_tools_enabled = use_builtin_tools and model_builtin_tools.get('skills', True)
    listed = (
        await services.get_terminal_json(request, user, metadata, '/skills', extra_params)
        if terminal_id or terminal_skill_ids
        else None
    )
    listed_skills = listed if isinstance(listed, list) else []
    if terminal_id and skill_tools_enabled:
        terminal_skills = listed_skills
    else:
        by_id = {skill['id']: skill for skill in listed_skills}
        terminal_skills = [by_id[sid] for sid in terminal_skill_ids if sid in by_id]

    manifest = []
    for skill in terminal_skills:
        if skill['id'] in activated_skill_ids or not skill_tools_enabled:
            await _append_terminal_skill_instructions(
                services, skill, request, user, metadata, extra_params, skill_tools_enabled, form_data, adapters
            )
        else:
            manifest.append(services.format_terminal_skill_manifest_entry(skill))
    return terminal_skills, ''.join(manifest)


async def _append_terminal_skill_instructions(
    services, skill, request, user, metadata, extra_params, tools_enabled, form_data, adapters
):
    skill_id = skill['id']
    skill_name = unquote(skill_id.removeprefix('terminal:'))
    loaded = await services.get_terminal_skill(
        request,
        user.model_dump(),
        metadata,
        skill_name,
        extra_params,
        overview=True,
        tools_enabled=tools_enabled,
    )
    if loaded:
        form_data['messages'] = adapters.add_or_update_system_message(
            services.format_terminal_skill_context(loaded, skill_id, tools_enabled),
            form_data['messages'],
            append=True,
        )
