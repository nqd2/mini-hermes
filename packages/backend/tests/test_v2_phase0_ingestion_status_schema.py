"""Validate the proposed Phase 0 ingestion status shape, not a V2 worker."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMAS = Path(__file__).resolve().parents[3] / 'v2_draft' / 'phase0' / 'schemas'


@pytest.fixture
def validator():
    schema = json.loads((SCHEMAS / 'ingestion-status.schema.json').read_text(encoding='utf-8'))
    coverage = json.loads((SCHEMAS / 'coverage-proof.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    registry = Registry().with_resource(coverage['$id'], Resource.from_contents(coverage))
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


@pytest.fixture
def staged():
    return {
        'contract_version': '2.0-draft',
        'job_id': 'job-1', 'document_id': 'doc-1', 'revision': 1,
        'source_sha256': None, 'source_state': 'STAGED', 'integrity_status': 'UNKNOWN',
        'index_state': 'NOT_REQUESTED', 'index_build_id': None,
        'parser_coverage': None, 'index_coverage': None, 'problem': None,
        'updated_at': '2026-11-13T00:00:00Z',
    }


def coverage(kind):
    return {
        'coverage_type': kind,
        'scope': {
            'document_id': 'doc-1', 'revision': 1, 'source_sha256': 'a' * 64,
            'index_build_id': 'build-1', 'unit_type': 'cell', 'scope_id': 'sheet-1',
        },
        'enumerated_total': 5, 'processed_intervals': [[0, 3]], 'processed_count': 3,
        'failed_units': ['cell-4'], 'unsupported_units': [], 'excluded_units': [],
        'complete': False, 'proof_id': f'{kind}-proof', 'verified_at': '2026-11-13T00:00:00Z',
    }


def test_staged_and_ready_with_partial_coverage_are_structurally_valid(validator, staged):
    validator.validate(staged)
    ready = copy.deepcopy(staged)
    ready.update(
        source_sha256='a' * 64, source_state='AVAILABLE', integrity_status='VERIFIED',
        index_state='READY', index_build_id='build-1',
        parser_coverage=coverage('parser_coverage'), index_coverage=coverage('index_coverage'),
    )
    validator.validate(ready)
    assert ready['parser_coverage']['complete'] is False
    assert ready['index_coverage']['complete'] is False


@pytest.mark.parametrize('change', [
    {'revision': 2},
    {'source_sha256': 'a' * 64},
    {'source_state': 'AVAILABLE'},
    {'index_state': 'READY'},
    {'index_state': 'RUNNING'},
    {'integrity_status': 'OK'},
    {'updated_at': 'yesterday'},
])
def test_invalid_status_or_unproven_transition_is_rejected(validator, staged, change):
    staged.update(change)
    assert list(validator.iter_errors(staged))


def test_ready_requires_independent_coverage_proofs(validator, staged):
    staged.update(
        source_sha256='a' * 64, source_state='AVAILABLE', integrity_status='VERIFIED',
        index_state='READY', index_build_id='build-1',
    )
    assert list(validator.iter_errors(staged))


def test_ready_rejects_a_coverage_claim_without_required_provenance(validator, staged):
    staged.update(
        source_sha256='a' * 64, source_state='AVAILABLE', integrity_status='VERIFIED',
        index_state='READY', index_build_id='build-1',
        parser_coverage=coverage('parser_coverage'), index_coverage=coverage('index_coverage'),
    )
    staged['index_coverage']['complete'] = True
    assert list(validator.iter_errors(staged))
