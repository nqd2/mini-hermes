"""Validate the proposed Phase 0 coverage schema, not a V2 implementation."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / 'v2_draft' / 'phase0' / 'schemas' / 'coverage-proof.schema.json'
)


@pytest.fixture
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.fixture
def proof():
    return {
        'coverage_type': 'retrieval_coverage',
        'scope': {
            'document_id': 'doc-1', 'revision': 2, 'source_sha256': 'a' * 64,
            'index_build_id': 'build-1', 'unit_type': 'cell', 'scope_id': 'sheet-1',
        },
        'enumerated_total': 100,
        'processed_intervals': [[0, 40], [40, 100]],
        'processed_count': 100,
        'failed_units': [],
        'unsupported_units': [],
        'excluded_units': [],
        'complete': True,
        'proof_id': 'proof-1',
        'verified_at': '2026-11-12T00:00:00Z',
    }


def test_valid_complete_and_unknown_partial_proofs(validator, proof):
    validator.validate(proof)
    partial = copy.deepcopy(proof)
    partial.update(enumerated_total=None, complete=False, processed_count=0, processed_intervals=[])
    validator.validate(partial)


@pytest.mark.parametrize('change', [
    {'enumerated_total': None},
    {'failed_units': ['cell-1']},
    {'unsupported_units': ['cell-1']},
    {'excluded_units': ['cell-1']},
    {'coverage_type': 'model_claim'},
    {'processed_intervals': [[0, -1]]},
    {'verified_at': 'not-a-timestamp'},
])
def test_invalid_complete_coverage_is_rejected(validator, proof, change):
    proof.update(change)
    assert list(validator.iter_errors(proof))


def test_missing_provenance_and_unknown_fields_are_rejected(validator, proof):
    del proof['scope']['source_sha256']
    proof['unverified_claim'] = True
    errors = list(validator.iter_errors(proof))
    assert len(errors) >= 2


def test_documented_coverage_example_matches_the_proposed_schema(validator):
    document = SCHEMA_PATH.parents[1] / '05-coverage-semantics.md'
    example = document.read_text(encoding='utf-8').split('```json\n', 1)[1].split('\n```', 1)[0]
    validator.validate(json.loads(example))
