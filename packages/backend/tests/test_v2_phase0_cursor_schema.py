"""Validate the Phase 0 cursor payload shape; this is not a V2 codec test."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / 'v2_draft' / 'phase0' / 'schemas' / 'cursor-payload.schema.json'
)


@pytest.fixture
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def payload():
    return {
        'v': 1,
        'principal_id': 'user-1',
        'tenant_id': 'tenant-1',
        'operation': 'read_spreadsheet_range',
        'document_id': 'doc-1',
        'revision': 2,
        'source_sha256': 'a' * 64,
        'build_id': 'build-1',
        'scope_sha256': 'b' * 64,
        'position': {'item_ordinal': 4, 'fragment': None},
        'iat': 1_700_000_000,
        'exp': 1_700_000_900,
    }


def test_boundary_and_fragment_positions_are_structurally_valid(validator, payload):
    validator.validate(payload)
    fragmented = copy.deepcopy(payload)
    fragmented['position']['fragment'] = {'field': 'raw_value', 'codepoint_offset': 17}
    validator.validate(fragmented)


@pytest.mark.parametrize('change', [
    {'v': 2},
    {'operation': 'commit_document_edit'},
    {'revision': 0},
    {'source_sha256': 'invalid'},
    {'scope_sha256': 'A' * 64},
    {'position': {'item_ordinal': -1, 'fragment': None}},
    {'position': {'item_ordinal': 0, 'fragment': {'field': 'raw_value', 'codepoint_offset': -1}}},
    {'position': {'item_ordinal': 0, 'fragment': {'field': 'raw_value', 'codepoint_offset': 1, 'extra': True}}},
])
def test_invalid_cursor_payload_shape_is_rejected(validator, payload, change):
    payload.update(change)
    assert list(validator.iter_errors(payload))


def test_missing_binding_and_extra_authority_are_rejected(validator, payload):
    del payload['principal_id']
    payload['permission_granted'] = True
    assert len(list(validator.iter_errors(payload))) >= 2
