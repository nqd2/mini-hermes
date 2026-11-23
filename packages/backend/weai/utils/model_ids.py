from __future__ import annotations

import re
from typing import Any

_NON_TEXT_MODEL_TYPES = frozenset(
    {
        'asr',
        'audio',
        'audio-to-audio',
        'automatic-speech-recognition',
        'classification',
        'embedding',
        'embeddings',
        'feature-extraction',
        'image',
        'image-generation',
        'image-to-image',
        'image-to-video',
        'inpainting',
        'moderation',
        'rerank',
        'reranker',
        'sentence-similarity',
        'speech-to-text',
        'stt',
        'text-classification',
        'text-ranking',
        'text-to-audio',
        'text-to-image',
        'text-to-speech',
        'text-to-video',
        'token-classification',
        'tts',
        'video',
        'video-generation',
        'zero-shot-classification',
    }
)

_NON_TEXT_MODEL_ID_PATTERN = re.compile(
    r'(?:'
    r'embed'
    r'|(?:^|[/:._-])(?:bge|e5|gte)-(?!.*(?:chat|instruct))'
    r'|text-similarity'
    r'|(?:text|code)-search-'
    r'|rerank'
    r'|moderation'
    r'|whisper'
    r'|(?:^|[/:._-])tts(?:$|[/:._\-\d])'
    r'|transcribe'
    r'|diarize'
    r'|realtime'
    r'|audio-preview'
    r'|gpt-audio'
    r'|text-to-speech'
    r'|speech-to-text'
    r'|(?:^|[/:._-])(?:elevenlabs|suno|udio|lyria)(?:$|[/:._\-\d])'
    r'|dall-e'
    r'|(?:gpt|chatgpt)-image'
    r'|(?:^|[/:._-])imagen(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])flux(?:$|[/:._\-\d])'
    r'|stable-diffusion'
    r'|(?:^|[/:._-])sdxl(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])sd-?3(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])(?:midjourney|ideogram|recraft|playground-v2)(?:$|[/:._\-\d])'
    r'|image-generation'
    r'|text-to-image'
    r'|image-preview'
    r'|-image(?:-\d+)?(?:$|:)'
    r'|(?:^|[/:._-])(?:sora|veo|kling|runway|luma|pika|hailuo|cogvideo)(?:$|[/:._\-\d])'
    r'|hunyuan-video'
    r'|(?:text|image)-to-video'
    r'|(?:^|[/:._-])wan-?2(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])(?:babbage|davinci)(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])(?:text-)?curie(?:$|[/:._\-\d])'
    r'|(?:^|[/:._-])text-ada-(?:$|\d)'
    r')',
    re.IGNORECASE,
)


def strip_provider_model_prefix(model_id: str, prefix_id: str | None) -> str:
    if prefix_id and model_id.startswith(f'{prefix_id}.'):
        return model_id[len(f'{prefix_id}.') :]
    return model_id


def _has_non_text_metadata(source: dict[str, Any]) -> bool:
    architecture = source.get('architecture')
    if isinstance(architecture, dict):
        output_modalities = architecture.get('output_modalities')
        if isinstance(output_modalities, list) and output_modalities:
            normalized = {str(item).strip().lower() for item in output_modalities if item}
            if 'text' not in normalized:
                return True
        modality = architecture.get('modality')
        if isinstance(modality, str) and '->' in modality:
            _, output_part = modality.lower().split('->', 1)
            output_tokens = {token for token in re.split(r'[+,/\s]+', output_part.strip()) if token}
            if output_tokens and 'text' not in output_tokens:
                return True

    output_modalities = source.get('output_modalities')
    if isinstance(output_modalities, list) and output_modalities:
        normalized = {str(item).strip().lower() for item in output_modalities if item}
        if 'text' not in normalized:
            return True

    for field in ('type', 'model_type', 'task', 'pipeline_tag', 'subtype'):
        value = source.get(field)
        if isinstance(value, str) and value.strip().lower() in _NON_TEXT_MODEL_TYPES:
            return True

    capabilities = source.get('capabilities')
    if isinstance(capabilities, dict):
        text_keys = [key for key in ('chat', 'completion', 'completion_chat', 'text') if key in capabilities]
        if text_keys and all(capabilities.get(key) is False for key in text_keys):
            return True
        if (capabilities.get('embedding') is True or capabilities.get('embeddings') is True) and not any(
            capabilities.get(key) is True for key in text_keys
        ):
            return True

    return False


def is_text_generation_model(model: dict[str, Any] | str | None, *, allow_manual_override: bool = False) -> bool:
    """Return True when *model* is a text-generation model suitable for chat/agent selectors."""
    if isinstance(model, str):
        model_id = model.strip()
        if not model_id:
            return False
        return True if allow_manual_override else not bool(_NON_TEXT_MODEL_ID_PATTERN.search(model_id))

    if not isinstance(model, dict):
        return False

    raw_id = model.get('id') or model.get('model_id') or model.get('name') or ''
    model_id = str(raw_id).strip()
    if not model_id:
        return False

    if _has_non_text_metadata(model):
        return False

    nested_openai = model.get('openai')
    if isinstance(nested_openai, dict) and _has_non_text_metadata(nested_openai):
        return False

    info = model.get('info')
    if isinstance(info, dict):
        meta = info.get('meta')
        if isinstance(meta, dict) and _has_non_text_metadata(meta):
            return False

    if allow_manual_override:
        return True

    return not bool(_NON_TEXT_MODEL_ID_PATTERN.search(model_id))
