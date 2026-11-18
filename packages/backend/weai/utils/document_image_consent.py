"""Scope and consent checks for document-bound agent image generation."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from weai.utils.json_codec import JSONCodec


OFFICE_DOCUMENT_EXTENSIONS = frozenset({'.docx', '.docm', '.xlsx', '.xlsm', '.pptx', '.pptm'})
IMAGE_GENERATION_QUESTION_ID = 'document_image_generation'
IMAGE_GENERATION_APPROVE_LABEL = 'Generate image'


def is_office_document_context(metadata: dict[str, Any] | None) -> bool:
    """Return whether the active, unambiguous resource scope contains an Office document."""
    if not isinstance(metadata, dict) or not metadata.get('resource_aware_files'):
        return False
    if metadata.get('resource_scope_ambiguous'):
        return False

    resources = [item for item in metadata.get('resource_files') or [] if isinstance(item, dict)]
    scope_ids = {str(value) for value in metadata.get('resource_scope_ids') or [] if value is not None}
    if scope_ids:
        resources = [item for item in resources if str(item.get('id')) in scope_ids]

    return any(
        PurePath(str(item.get('name') or item.get('filename') or '')).suffix.lower() in OFFICE_DOCUMENT_EXTENSIONS
        for item in resources
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    try:
        for _ in range(2):
            if isinstance(value, str):
                value = JSONCodec.loads(value)
    except JSONCodec.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def has_approved_document_image_choice(output: list[dict] | Any) -> bool:
    """Find a completed, explicit image-generation answer in one saved assistant turn."""
    if not isinstance(output, list):
        return False

    approved_call_ids = set()
    for item in output:
        if not isinstance(item, dict) or item.get('type') != 'function_call' or item.get('name') != 'ask_user':
            continue
        arguments = _json_object(item.get('arguments'))
        questions = arguments.get('questions') if arguments else None
        if not isinstance(questions, list):
            continue
        question = next(
            (candidate for candidate in questions if isinstance(candidate, dict) and candidate.get('id') == IMAGE_GENERATION_QUESTION_ID),
            None,
        )
        options = question.get('options') if question else None
        if not isinstance(options, list) or not options or options[0].get('label') != IMAGE_GENERATION_APPROVE_LABEL:
            continue
        call_id = item.get('call_id') or item.get('id')
        if call_id:
            approved_call_ids.add(str(call_id))

    for item in output:
        if not isinstance(item, dict) or item.get('type') != 'function_call_output':
            continue
        if str(item.get('call_id') or '') not in approved_call_ids:
            continue
        parts = item.get('output')
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
            continue
        answer_payload = _json_object(parts[0].get('text'))
        answers = answer_payload.get('answers') if answer_payload and answer_payload.get('status') == 'answered' else None
        answer = answers.get(IMAGE_GENERATION_QUESTION_ID) if isinstance(answers, dict) else None
        if (
            isinstance(answer, dict)
            and answer.get('type') == 'option'
            and answer.get('option_index') == 0
            and answer.get('label') == IMAGE_GENERATION_APPROVE_LABEL
        ):
            return True
    return False
