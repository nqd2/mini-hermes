"""Cross-runtime reference vectors for proposed V2 hashes, not production code."""

import hashlib
import json
from pathlib import Path

import pytest


VECTORS = Path(__file__).resolve().parents[3] / 'v2_draft' / 'phase0' / 'fixtures' / 'hash-vectors-v1.json'


def _candidate_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def test_proposed_golden_vectors_match_canonical_bytes_and_domain_hash():
    vectors = json.loads(VECTORS.read_text(encoding='utf-8'))
    assert {item['kind'] for item in vectors} == {'patch', 'request'}
    for item in vectors:
        canonical = _candidate_bytes(item['payload'])
        assert canonical.decode('utf-8') == item['canonical_json']
        assert hashlib.sha256(item['domain'].encode('ascii') + canonical).hexdigest() == item['sha256']


def test_candidate_serialization_fails_closed_on_nonfinite_or_invalid_unicode():
    with pytest.raises(ValueError):
        _candidate_bytes({'value': float('nan')})
    with pytest.raises(ValueError):
        _candidate_bytes({'value': float('inf')})
    with pytest.raises(UnicodeEncodeError):
        _candidate_bytes({'value': '\ud800'})


def test_hash_domain_separation_changes_digest_for_identical_canonical_payload():
    canonical = _candidate_bytes({'operation': 'example'})
    patch = hashlib.sha256(b'mhv2:patch:v1\n' + canonical).hexdigest()
    request = hashlib.sha256(b'mhv2:request:v1\n' + canonical).hexdigest()
    assert patch != request
