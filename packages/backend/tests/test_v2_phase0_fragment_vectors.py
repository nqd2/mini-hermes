"""Review proposed coverage vectors using a test-only reference oracle.

This is not an implementation or validation of V2 provenance, authentication,
or output behavior. Run these vectors against the actual service after it exists.
"""

import json
from pathlib import Path

import pytest

VECTORS = (Path(__file__).resolve().parents[3] / 'v2_draft' / 'phase0'
           / 'fixtures' / 'fragment-coverage-vectors.json')
CASES = json.loads(VECTORS.read_text(encoding='utf-8'))['cases']


def reference_complete(case):
    """A bounded mathematical oracle over synthetic, already-trusted evidence."""
    total = case['total']
    if total is None or total < 0:
        return False
    if not set(case['required_fields']).issubset(case['observed_fields']):
        return False
    if total == 0:
        return case['empty_observed'] and bool(case['empty_evidence']) and not case['fragments']
    intervals = []
    for fragment in case['fragments']:
        if fragment['scope'] != case['scope'] or not fragment['evidence']:
            return False
        start, end = fragment['start'], fragment['end']
        if not 0 <= start < end <= total:
            return False
        intervals.append((start, end))
    end = 0
    for start, stop in sorted(intervals):
        if start > end:
            return False
        end = max(end, stop)
    return end == total


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_proposed_fragment_vectors_against_reference_oracle(case):
    assert reference_complete(case) is case['expected']


def test_vector_names_are_unique():
    assert len({case['name'] for case in CASES}) == len(CASES)


def test_unicode_vector_counts_codepoints_not_utf8_bytes_or_json_escapes():
    case = next(case for case in CASES if case['name'] == 'unicode-codepoint-offsets')
    text = case['sample_text']
    assert len(text) == case['total'] == 4
    assert len(text.encode('utf-8')) > case['total']
    assert len(json.dumps(text, ensure_ascii=True)) > case['total']
