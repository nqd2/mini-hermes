"""Cached OpenRouter model-author & provider icon registry for admin model selectors."""

import asyncio
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout

from weai.env import AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST

OPENROUTER_ORIGIN = 'https://openrouter.ai'
OPENROUTER_ALL_PROVIDERS_URL = f'{OPENROUTER_ORIGIN}/api/frontend/v1/all-providers'
OPENROUTER_CATALOG_MODELS_URL = f'{OPENROUTER_ORIGIN}/api/frontend/v1/catalog/models'
CACHE_TTL_SECONDS = 12 * 3600

# Canonical model-author icons matching https://openrouter.ai/models
# (all-providers lists inference hosts like google-vertex or alibaba rather than model brands).
CANONICAL_MODEL_AUTHOR_ICONS: dict[str, dict] = {
    'openai': {'url': f'{OPENROUTER_ORIGIN}/images/icons/OpenAI.svg', 'invert_dark': True},
    'anthropic': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Anthropic.svg', 'invert_dark': True},
    'google': {'url': f'{OPENROUTER_ORIGIN}/images/icons/GoogleGemini.svg', 'invert_dark': False},
    'qwen': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Qwen.png', 'invert_dark': False},
    'meta-llama': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Meta.png', 'invert_dark': False},
    'meta': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Meta.png', 'invert_dark': False},
    'deepseek': {'url': f'{OPENROUTER_ORIGIN}/images/icons/DeepSeek.png', 'invert_dark': False},
    'mistralai': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Mistral.png', 'invert_dark': False},
    'mistral': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Mistral.png', 'invert_dark': False},
    'cohere': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Cohere.png', 'invert_dark': False},
    'perplexity': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Perplexity.svg', 'invert_dark': True},
    'microsoft': {'url': f'{OPENROUTER_ORIGIN}/images/icons/Microsoft.svg', 'invert_dark': False},
}

_IMAGE_EXTENSIONS = ('.svg', '.png', '.webp', '.jpg', '.jpeg', '.ico')

_icons_cache: dict[str, dict] = {}
_icons_expires_at = 0.0
_icons_lock = asyncio.Lock()


def _normalize_icon_url(raw_url: str | None, *, bare_filename_in_icons: bool = False) -> str | None:
    if not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    if not url:
        return None
    if url.startswith('/'):
        url = f'{OPENROUTER_ORIGIN}{url}'
    elif bare_filename_in_icons and '://' not in url and not url.startswith('data:'):
        if url.lower().split('?', 1)[0].endswith(_IMAGE_EXTENSIONS):
            url = f'{OPENROUTER_ORIGIN}/images/icons/{url}'
        else:
            return None
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc:
        return None
    if bare_filename_in_icons:
        path_lower = parsed.path.lower()
        if not path_lower.endswith(_IMAGE_EXTENSIONS) and 'favicon' not in url.lower():
            return None
    return url


def parse_all_providers_payload(body: object) -> dict[str, dict]:
    if not isinstance(body, dict):
        return {}
    entries = body.get('data')
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = entry.get('slug')
        icon = entry.get('icon')
        if not isinstance(slug, str) or not slug.strip() or not isinstance(icon, dict):
            continue
        url = _normalize_icon_url(icon.get('url'))
        if not url:
            continue
        class_name = icon.get('className') if isinstance(icon.get('className'), str) else ''
        result[slug.strip().lower()] = {
            'url': url,
            'invert_dark': 'dark:invert' in class_name,
        }
    return result


def parse_catalog_author_icons(body: object) -> dict[str, dict]:
    if not isinstance(body, dict):
        return {}
    entries = body.get('data')
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        author = entry.get('author')
        raw_uri = entry.get('author_icon_uri')
        if not isinstance(author, str) or not author.strip():
            continue
        slug = author.strip().lower()
        if slug in result:
            continue
        url = _normalize_icon_url(raw_uri, bare_filename_in_icons=True)
        if not url:
            continue
        result[slug] = {'url': url, 'invert_dark': False}
    return result


async def get_openrouter_provider_icons(*, force_refresh: bool = False) -> dict[str, dict]:
    global _icons_cache, _icons_expires_at
    if not force_refresh and _icons_cache and time.monotonic() < _icons_expires_at:
        return _icons_cache
    async with _icons_lock:
        if not force_refresh and _icons_cache and time.monotonic() < _icons_expires_at:
            return _icons_cache
        from weai.utils.session_pool import get_session

        merged: dict[str, dict] = {}
        try:
            session = await get_session()
            async with session.get(
                OPENROUTER_ALL_PROVIDERS_URL,
                timeout=ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
                allow_redirects=False,
            ) as response:
                if response.status == 200:
                    merged.update(parse_all_providers_payload(await response.json()))
        except (ClientError, OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
            pass

        try:
            session = await get_session()
            async with session.get(
                OPENROUTER_CATALOG_MODELS_URL,
                timeout=ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
                allow_redirects=False,
            ) as response:
                if response.status == 200:
                    merged.update(parse_catalog_author_icons(await response.json()))
        except (ClientError, OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
            pass

        # Canonical model-author icons always win for major model brands
        merged.update(CANONICAL_MODEL_AUTHOR_ICONS)
        _icons_cache = merged
        _icons_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return _icons_cache
