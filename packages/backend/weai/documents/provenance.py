"""Stable identifiers for evidence extracted from mutable resources."""

from __future__ import annotations

import hashlib


def content_version(content: str | bytes) -> str:
    """Return a content-addressed version suitable for provenance metadata."""
    value = content if isinstance(content, bytes) else content.encode('utf-8', errors='replace')
    return hashlib.sha256(value).hexdigest()


def evidence_id(
    resource_id: str,
    version: str,
    start: int | None,
    end: int | None,
    excerpt: str = '',
) -> str:
    """Identify a source span; edits to the resource produce a new identifier."""
    identity = '\0'.join(
        (
            str(resource_id),
            str(version),
            '' if start is None else str(start),
            '' if end is None else str(end),
            excerpt,
        )
    )
    return hashlib.sha256(identity.encode('utf-8', errors='replace')).hexdigest()[:32]
