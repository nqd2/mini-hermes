"""WeAI release API and local responses for legacy callers."""

import logging
import os
from html import escape

from fastapi import APIRouter, Depends, HTTPException

from weai.releases.release_manifest import ReleaseEntry, latest_release_version, load_release, load_releases
from weai.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


def configured_release() -> ReleaseEntry:
    try:
        version = os.environ.get('WEAI_RELEASE_VERSION') or latest_release_version()
        return load_release(version)
    except (OSError, ValueError) as exc:
        log.error('Unable to load WeAI release notes: %s', exc)
        raise HTTPException(status_code=503, detail='WeAI release notes are unavailable') from exc


@router.get('/release', response_model=ReleaseEntry)
async def get_release(_user=Depends(get_verified_user)) -> ReleaseEntry:
    return configured_release()


@router.get('/releases', response_model=list[ReleaseEntry])
async def get_releases(_user=Depends(get_verified_user)) -> list[ReleaseEntry]:
    current = configured_release()
    try:
        max_version = None if current.version == 'dev' else current.version
        return load_releases(max_version=max_version)
    except (OSError, ValueError) as exc:
        log.error('Unable to load WeAI release history: %s', exc)
        raise HTTPException(status_code=503, detail='WeAI release notes are unavailable') from exc


def local_changelog() -> dict:
    release = configured_release()
    if release.released_at is None:
        return {}
    return {
        release.version: {
            'date': release.released_at.isoformat(),
            **{
                section.lower(): [{'title': '', 'content': item, 'raw': f'<li>{escape(item)}</li>'} for item in items]
                for section, items in release.sections.items()
            },
        }
    }
