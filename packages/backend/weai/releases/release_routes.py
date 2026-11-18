"""WeAI release API and local responses for legacy callers."""

import logging
import os
from html import escape

from fastapi import APIRouter, Depends, HTTPException

from weai.releases.release_manifest import ReleaseEntry, load_release
from weai.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


def configured_release() -> ReleaseEntry:
    try:
        return load_release(os.environ.get('WEAI_RELEASE_VERSION', 'dev'))
    except (OSError, ValueError) as exc:
        log.error('Unable to load WeAI release notes: %s', exc)
        raise HTTPException(status_code=503, detail='WeAI release notes are unavailable') from exc


@router.get('/release', response_model=ReleaseEntry)
async def get_release(_user=Depends(get_verified_user)) -> ReleaseEntry:
    return configured_release()


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
