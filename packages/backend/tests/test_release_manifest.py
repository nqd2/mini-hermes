import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'weai' / 'releases'))
from release_manifest import latest_release_version, load_release, load_releases, validate_release_build


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


def test_latest_release_version_reads_highest_semver(tmp_path):
    manifest = tmp_path / 'releases.json'
    entry = {'released_at': '2026-11-18', 'sections': {'Added': ['Notes']}}
    manifest.write_text(json.dumps({'0.1.0': entry, '0.10.0': entry, '0.2.0': entry}))
    assert latest_release_version(manifest) == '0.10.0'


def test_release_0_1_3_and_load_releases_order():
    validate_release_build('0.1.3')
    entry = load_release('0.1.3')
    assert entry.released_at == date(2026, 11, 27)
    assert set(entry.sections) == {'Added', 'Improved'}

    all_releases = load_releases()
    assert [item.version for item in all_releases][:4] == ['0.1.3', '0.1.2', '0.1.1', '0.1.0']

    capped = load_releases(max_version='0.1.1')
    assert [item.version for item in capped] == ['0.1.1', '0.1.0']
