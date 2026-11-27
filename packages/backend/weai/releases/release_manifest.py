"""Load WeAI release notes from the build's local manifest."""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name('releases.json')
VERSION_PATTERN = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?')
MARKUP_OR_LINK = re.compile(r'[<>]|(?:https?://|www\.|[a-z][a-z0-9+.-]*://|mailto:)|\]\(', re.I)


@dataclass(frozen=True)
class ReleaseEntry:
    version: str
    released_at: date | None
    sections: dict[str, list[str]]


def _plain_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not MARKUP_OR_LINK.search(value)


def load_release(version: str, manifest_path: Path | None = None) -> ReleaseEntry:
    if version == 'dev':
        return ReleaseEntry(version, None, {})
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f'Invalid WeAI release version {version!r}')

    manifest = json.loads((manifest_path or MANIFEST_PATH).read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or version not in manifest:
        raise ValueError(f'WeAI release {version!r} is missing from the manifest')
    entry = manifest[version]
    if not isinstance(entry, dict) or set(entry) != {'released_at', 'sections'}:
        raise ValueError(f'Invalid manifest entry for WeAI release {version!r}')
    released_at = entry['released_at']
    if not isinstance(released_at, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', released_at):
        raise ValueError(f'Invalid release date for WeAI release {version!r}')
    sections = entry['sections']
    if (
        not isinstance(sections, dict)
        or not sections
        or any(
            not _plain_text(title)
            or not isinstance(items, list)
            or not items
            or not all(_plain_text(item) for item in items)
            for title, items in sections.items()
        )
    ):
        raise ValueError(f'WeAI release {version!r} must have plain-text sections and notes')
    return ReleaseEntry(version, date.fromisoformat(released_at), sections)


def _version_sort_key(version: str) -> tuple[int, int, int, int, str]:
    match = re.fullmatch(r'([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([A-Za-z0-9.-]+))?', version)
    if not match:
        raise ValueError(f'Invalid WeAI release version {version!r}')
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), 1 if pre is None else 0, pre or '')


def latest_release_version(manifest_path: Path | None = None) -> str:
    manifest = json.loads((manifest_path or MANIFEST_PATH).read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError('WeAI release manifest is empty')
    return max(manifest.keys(), key=_version_sort_key)


def load_releases(max_version: str | None = None, manifest_path: Path | None = None) -> list[ReleaseEntry]:
    manifest = json.loads((manifest_path or MANIFEST_PATH).read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('Invalid WeAI release manifest')
    max_key = _version_sort_key(max_version) if max_version and max_version != 'dev' else None
    versions = sorted(manifest.keys(), key=_version_sort_key, reverse=True)
    entries: list[ReleaseEntry] = []
    for version in versions:
        if max_key is not None and _version_sort_key(version) > max_key:
            continue
        entries.append(load_release(version, manifest_path))
    return entries


def validate_release_build(version: str, manifest_path: Path = MANIFEST_PATH) -> None:
    load_release(version, manifest_path)
