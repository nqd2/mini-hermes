import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'weai' / 'releases'))
from release_manifest import load_release, validate_release_build


def test_dev_has_no_notes():
    entry = load_release('dev')
    assert entry.released_at is None
    assert entry.sections == {}


def test_checked_in_version():
    entry = load_release('0.1.0')
    assert entry.released_at == date(2026, 11, 18)
    assert entry.sections['Added']


@pytest.mark.parametrize(
    'override',
    [
        {'released_at': '2026-02-30'},
        {'sections': []},
        {'sections': {'<b>Added</b>': ['Notes']}},
        {'sections': {'Added': ['<b>Notes</b>']}},
        {'sections': {'Added': ['Read https://example.com']}},
        {'sections': {'Added': ['[Notes](example.com)']}},
    ],
)
def test_rejects_invalid_manifest_entry(tmp_path, override):
    manifest = tmp_path / 'releases.json'
    entry = {'released_at': '2026-11-18', 'sections': {'Added': ['Notes']}}
    manifest.write_text(json.dumps({'1.0.0': entry | override}))
    with pytest.raises(ValueError):
        validate_release_build('1.0.0', manifest)


def test_rejects_mismatched_release():
    with pytest.raises(ValueError, match='missing'):
        validate_release_build('99.0.0')
