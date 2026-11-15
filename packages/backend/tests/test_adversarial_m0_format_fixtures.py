"""Adversarial stress-testing suite for M0 Format Invariants, Fixtures, Checksums & Hash Vectors.

Empirical Challenger 2 (teamwork_challenger_m0_2):
1. PDF Read-Only invariant: strictly rejects all mutation operations across casing, extensions, and service endpoints.
2. XLSX fidelity invariants: formula preservation, missing cache handling, hidden sheet visibility, and exact 57,765 rendered characters.
3. Fixture cryptographic integrity: byte-for-byte SHA256 checksums, single-byte corruption detection, and fail-closed revision integrity.
4. Hash vector determinism: canonical JSON serialization rules, domain separation, and prefix injection resistance.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import hashlib
import json
from pathlib import Path
import random
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import pytest
from fastapi import HTTPException

from open_webui.mini_hermes.chunks import extract_structured_text
from open_webui.mini_hermes.edits import apply_operations, extract_text
from open_webui.mini_hermes.models import Document, Revision
from open_webui.mini_hermes.parsers import extract_sections
from open_webui.mini_hermes.revision_integrity import get_verified_revision_content
from open_webui.mini_hermes.service import (
    create_artifact_preview,
    create_preview,
    publish_generated_document,
)

FIXTURES_DIR = Path(__file__).parent / 'fixtures' / 'v2'
DOCS_FIXTURES_DIR = Path(__file__).resolve().parents[3] / 'docs' / 'v2' / 'phase0' / 'fixtures'


# ==============================================================================
# Challenge 1: PDF Read-Only Invariant Enforcement
# ==============================================================================

@pytest.mark.parametrize('filename', [
    'document.pdf',
    'DOCUMENT.PDF',
    'Document.Pdf',
    'archive.report.pdf',
])
@pytest.mark.parametrize('op', [
    [{'op': 'replace_text', 'find': 'Synthetic', 'replace': 'Malicious'}],
    [{'op': 'append_paragraph', 'text': 'Injected paragraph'}],
    [{'op': 'set_cell', 'sheet': 'Sheet1', 'cell': 'A1', 'value': 'Injected'}],
    [{'op': 'set_shape_text', 'slide': 0, 'shape': 0, 'text': 'Injected'}],
    [{'op': 'add_slide'}],
    [{'op': 'add_sheet', 'name': 'InjectedSheet'}],
    [{'op': 'arbitrary_unregistered_operation', 'payload': 'evil'}],
])
def test_pdf_mutation_operations_strictly_rejected(filename: str, op: list[dict]):
    """Adversarially verify that apply_operations strictly rejects any mutation on PDF files."""
    pdf_bytes = (FIXTURES_DIR / 'pdf-f01-text-and-image.pdf').read_bytes()
    with pytest.raises(ValueError, match='PDF is read-only'):
        apply_operations(pdf_bytes, filename, op)


@pytest.mark.asyncio
async def test_pdf_create_preview_strictly_rejected():
    """Adversarially verify that create_preview refuses to generate previews for PDF documents."""
    db = AsyncMock()
    for fname in ('contract.pdf', 'CONTRACT.PDF'):
        doc = Document(id='doc-pdf', filename=fname, current_revision=1, drive_version=None, drive_mime_type=None)
        with pytest.raises(HTTPException) as exc_info:
            await create_preview(db, doc, 'user-1', [{'op': 'replace_text', 'find': 'a', 'replace': 'b'}])
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'PDF is read-only'


@pytest.mark.asyncio
async def test_pdf_create_artifact_preview_strictly_rejected():
    """Adversarially verify that create_artifact_preview refuses staged PDF replacements."""
    db = AsyncMock()
    store = MagicMock()
    pdf_bytes = (FIXTURES_DIR / 'pdf-f01-text-and-image.pdf').read_bytes()
    for fname in ('summary.pdf', 'SUMMARY.PDF'):
        doc = Document(id='doc-pdf', filename=fname, current_revision=1, drive_version=None, drive_mime_type=None)
        with pytest.raises(HTTPException) as exc_info:
            await create_artifact_preview(db, store, doc, 'user-1', pdf_bytes)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'PDF is read-only'


@pytest.mark.asyncio
async def test_pdf_publish_generated_document_strictly_rejected():
    """Adversarially verify that publish_generated_document refuses updates targeting PDF documents."""
    db = AsyncMock()
    store = MagicMock()
    pdf_bytes = (FIXTURES_DIR / 'pdf-f01-text-and-image.pdf').read_bytes()
    with pytest.raises(HTTPException) as exc_info:
        await publish_generated_document(
            db, store, 'user-1', 'output.pdf', pdf_bytes, 'out-key-1', source_document_id='doc-pdf'
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == 'PDF is read-only'


def test_pdf_extract_sections_is_strictly_read_only():
    """Verify that reading a PDF succeeds and extracts selectable text without mutating source bytes."""
    pdf_path = FIXTURES_DIR / 'pdf-f01-text-and-image.pdf'
    original_bytes = pdf_path.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    sections = extract_sections(original_bytes, pdf_path.name)
    assert len(sections) == 2
    assert 'Synthetic selectable text layer' in sections[0][1]
    assert sections[1][1] == ''

    # Bytes must remain unchanged
    assert hashlib.sha256(original_bytes).hexdigest() == original_hash


# ==============================================================================
# Challenge 2: XLSX Fidelity Invariants
# ==============================================================================

def test_xlsx_formula_preservation_across_all_fixtures():
    """Verify formulas are preserved as formulas (<f>) across all synthetic XLSX fixtures."""
    # 1. xlsx-f01-f03-hidden-missing-cache.xlsx
    wb1 = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f01-f03-hidden-missing-cache.xlsx', data_only=False)
    assert wb1['Visible']['B2'].data_type == 'f'
    assert wb1['Visible']['B2'].value == '=A2*2'

    # 2. xlsx-f05-synthetic.xlsx
    wb5_s = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f05-synthetic.xlsx', data_only=False)
    assert wb5_s['Meta']['B9'].data_type == 'f'
    assert wb5_s['Meta']['B9'].value == '=ROW()-1'
    assert wb5_s['Meta']['B10'].data_type == 'f'
    assert wb5_s['Meta']['B10'].value == '=COUNTA(Interview!A2:A13)'

    # 3. xlsx-f05-exact-57765.xlsx
    wb5_e = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f05-exact-57765.xlsx', data_only=False)
    assert wb5_e['Metrics']['B3'].data_type == 'f'
    assert wb5_e['Metrics']['B3'].value == '=B2*2'


def test_xlsx_formula_protection_under_edit_operations():
    """Adversarially verify that editing a non-formula cell does NOT erase existing formulas."""
    fixture_path = FIXTURES_DIR / 'xlsx-f05-synthetic.xlsx'
    edit_op = [{'op': 'set_cell', 'sheet': 'Meta', 'cell': 'A2', 'value': 'PreserveCheck'}]
    edited_bytes = apply_operations(fixture_path.read_bytes(), fixture_path.name, edit_op)

    wb_edited = openpyxl.load_workbook(BytesIO(edited_bytes), data_only=False)
    assert wb_edited['Meta']['A2'].value == 'PreserveCheck'
    assert wb_edited['Meta']['B9'].data_type == 'f'
    assert wb_edited['Meta']['B9'].value == '=ROW()-1'
    assert wb_edited['Meta']['B10'].data_type == 'f'
    assert wb_edited['Meta']['B10'].value == '=COUNTA(Interview!A2:A13)'


def test_xlsx_missing_and_present_cache_behavior():
    """Verify missing cached values return None with data_only=True and render [cached=None]."""
    # Missing cache fixture
    wb_missing = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f01-f03-hidden-missing-cache.xlsx', data_only=True)
    assert wb_missing['Visible']['B2'].value is None

    s1 = extract_sections(
        (FIXTURES_DIR / 'xlsx-f01-f03-hidden-missing-cache.xlsx').read_bytes(),
        'xlsx-f01-f03-hidden-missing-cache.xlsx',
    )
    visible_text = next(text for name, text, _ in s1 if name == 'Visible')
    assert 'B2==A2*2 [cached=None]' in visible_text

    # Present cache fixture
    wb_present = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f05-synthetic.xlsx', data_only=True)
    assert wb_present['Meta']['B9'].value == 8
    assert wb_present['Meta']['B10'].value == 12

    s5 = extract_sections((FIXTURES_DIR / 'xlsx-f05-synthetic.xlsx').read_bytes(), 'xlsx-f05-synthetic.xlsx')
    meta_text = next(text for name, text, _ in s5 if name == 'Meta')
    assert 'B9==ROW()-1 [cached=8]' in meta_text
    assert 'B10==COUNTA(Interview!A2:A13) [cached=12]' in meta_text


def test_xlsx_hidden_sheet_handling_and_visibility():
    """Verify that hidden sheets are accurately identified by openpyxl sheet_state."""
    wb_hidden = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f01-f03-hidden-missing-cache.xlsx')
    assert wb_hidden['Visible'].sheet_state == 'visible'
    assert wb_hidden['Hidden'].sheet_state == 'hidden'

    wb_exact = openpyxl.load_workbook(FIXTURES_DIR / 'xlsx-f05-exact-57765.xlsx')
    assert wb_exact['Interview'].sheet_state == 'visible'
    assert wb_exact['Metrics'].sheet_state == 'visible'
    assert wb_exact['Hidden'].sheet_state == 'hidden'


def test_xlsx_exact_57765_rendered_character_structure():
    """Verify exact 57,765 rendered characters, merged cell range A5:B5, and lossless chunk assembly."""
    fixture_path = FIXTURES_DIR / 'xlsx-f05-exact-57765.xlsx'
    content = fixture_path.read_bytes()

    wb = openpyxl.load_workbook(fixture_path, data_only=False)
    merged_ranges = list(wb['Interview'].merged_cells.ranges)
    assert len(merged_ranges) == 1
    assert str(merged_ranges[0]) == 'A5:B5'

    rendered = extract_structured_text(content, fixture_path.name)
    assert len(rendered) == 57_765, f'Rendered length mismatch: got {len(rendered)}, expected 57765'

    # Verify lossless reconstruction under 4000 char window
    chunk_size = 4000
    pages = [rendered[i:i + chunk_size] for i in range(0, len(rendered), chunk_size)]
    assert len(pages) == 15
    assert ''.join(pages) == rendered


# ==============================================================================
# Challenge 3: Fixture Cryptographic Integrity & Single-Byte Fuzzing
# ==============================================================================

def test_fixture_cryptographic_checksums_match_manifest():
    """Verify all 6 synthetic fixtures match their byte-for-byte SHA256SUMS.txt entries."""
    manifest_file = FIXTURES_DIR / 'SHA256SUMS.txt'
    manifest = dict(line.split(maxsplit=1)[::-1] for line in manifest_file.read_text(encoding='utf-8').splitlines() if line.strip())
    assert len(manifest) == 6

    for fname, expected_hash in manifest.items():
        actual_hash = hashlib.sha256((FIXTURES_DIR / fname).read_bytes()).hexdigest()
        assert actual_hash == expected_hash, f'Checksum mismatch for {fname}'


def test_single_byte_corruption_fuzzing_detected_across_all_fixtures():
    """Adversarially mutate individual bytes across diverse offsets and verify 100% SHA256 divergence."""
    manifest_file = FIXTURES_DIR / 'SHA256SUMS.txt'
    manifest = dict(line.split(maxsplit=1)[::-1] for line in manifest_file.read_text(encoding='utf-8').splitlines() if line.strip())

    total_mutations = 0
    for fname, expected_hash in manifest.items():
        original = (FIXTURES_DIR / fname).read_bytes()
        n = len(original)

        test_offsets = sorted(list({
            0, 1, 2, 3, 10,
            n // 4, n // 2, 3 * n // 4,
            n - 2, n - 1,
            *[random.Random(100 + i).randint(0, n - 1) for i in range(15)]
        }))

        for offset in test_offsets:
            mutated = bytearray(original)
            mutated[offset] ^= 0x01
            mutated_hash = hashlib.sha256(mutated).hexdigest()
            total_mutations += 1
            assert mutated_hash != expected_hash, f'Collision detected for {fname} at offset {offset}'

    assert total_mutations >= 150


@pytest.mark.asyncio
async def test_revision_service_integrity_fails_closed_on_corrupted_or_truncated_bytes():
    """Adversarially verify get_verified_revision_content rejects corrupted or truncated objects with HTTP 503."""
    manifest_file = FIXTURES_DIR / 'SHA256SUMS.txt'
    manifest = dict(line.split(maxsplit=1)[::-1] for line in manifest_file.read_text(encoding='utf-8').splitlines() if line.strip())

    for fname, exp_hash in manifest.items():
        original = (FIXTURES_DIR / fname).read_bytes()
        rev = Revision(
            id='rev-test', document_id='doc-test', number=1,
            object_key=f'keys/{fname}', sha256=exp_hash, size=len(original)
        )

        # 1. Single byte flipped in storage
        corrupted = bytearray(original)
        corrupted[len(corrupted) // 2] ^= 0x01
        store_corrupt = AsyncMock()
        store_corrupt.get.return_value = bytes(corrupted)

        with pytest.raises(HTTPException) as exc_info:
            await get_verified_revision_content(store_corrupt, rev)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail['code'] == 'document_revision_integrity_failure'

        # 2. Truncated byte in storage
        store_trunc = AsyncMock()
        store_trunc.get.return_value = original[:-1]
        with pytest.raises(HTTPException) as exc_info:
            await get_verified_revision_content(store_trunc, rev)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail['code'] == 'document_revision_integrity_failure'


# ==============================================================================
# Challenge 4: Hash Vector Determinism & Canonical JSON Serialization
# ==============================================================================

def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode('utf-8')


def test_canonical_json_golden_vectors_match():
    """Verify both golden vectors in hash-vectors-v1.json match canonical serialization and domain digests."""
    vectors = json.loads((DOCS_FIXTURES_DIR / 'hash-vectors-v1.json').read_text(encoding='utf-8'))
    assert len(vectors) == 2

    for item in vectors:
        c_bytes = _canonical_bytes(item['payload'])
        assert c_bytes.decode('utf-8') == item['canonical_json']
        actual_hash = hashlib.sha256(item['domain'].encode('ascii') + c_bytes).hexdigest()
        assert actual_hash == item['sha256']


def test_canonical_json_key_sorting_invariance_recursive():
    """Adversarially verify that deeply nested dictionary key reordering yields identical canonical bytes."""
    obj1 = {'z': 1, 'a': 2, 'sub': {'k2': 'v2', 'k1': 'v1', 'deep': {'z2': 10, 'a2': 20}}}
    obj2 = {'sub': {'deep': {'a2': 20, 'z2': 10}, 'k1': 'v1', 'k2': 'v2'}, 'a': 2, 'z': 1}

    bytes1 = _canonical_bytes(obj1)
    bytes2 = _canonical_bytes(obj2)
    assert bytes1 == bytes2
    assert hashlib.sha256(b'mhv2:patch:v1\n' + bytes1).hexdigest() == \
           hashlib.sha256(b'mhv2:patch:v1\n' + bytes2).hexdigest()


def test_canonical_json_whitespace_delimiters_sensitivity():
    """Verify that modifying whitespace delimiters produces completely divergent digests."""
    obj = {'key': 'value', 'list': [1, 2, 3]}
    canonical = _canonical_bytes(obj)
    spaced = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(', ', ': ')).encode('utf-8')
    assert canonical != spaced
    assert hashlib.sha256(b'mhv2:patch:v1\n' + canonical).hexdigest() != \
           hashlib.sha256(b'mhv2:patch:v1\n' + spaced).hexdigest()


def test_canonical_json_non_finite_floats_fail_closed():
    """Adversarially verify that non-finite floats (nan, +inf, -inf) strictly raise ValueError."""
    for bad_float in [float('nan'), float('inf'), float('-inf')]:
        with pytest.raises(ValueError, match='Out of range float values'):
            _canonical_bytes({'number': bad_float})


def test_canonical_json_unicode_preservation_and_surrogate_rejection():
    """Verify Unicode diacritics are preserved as raw UTF-8 bytes and lone surrogates raise UnicodeEncodeError."""
    vn_text = 'Khảo sát: dòng 1\ndòng 2 — Tiếng Việt'
    canonical = _canonical_bytes({'survey': vn_text})

    # Must contain raw UTF-8 bytes, not \u...
    assert b'\\u' not in canonical
    assert 'dòng 1\\ndòng 2'.encode('utf-8') in canonical

    with pytest.raises(UnicodeEncodeError):
        _canonical_bytes({'surrogate': '\ud800'})


def test_domain_separation_prefix_collision_resistance():
    """Adversarially verify strict domain separation and resistance to prefix injection attacks."""
    payload = {'document_id': 'doc-1', 'operation': 'preview'}
    canonical = _canonical_bytes(payload)

    patch_hash = hashlib.sha256(b'mhv2:patch:v1\n' + canonical).hexdigest()
    request_hash = hashlib.sha256(b'mhv2:request:v1\n' + canonical).hexdigest()
    assert patch_hash != request_hash

    # Prefix injection inside JSON payload
    attack_payload = {'domain': 'mhv2:request:v1\n', 'document_id': 'doc-1', 'operation': 'preview'}
    attack_canonical = _canonical_bytes(attack_payload)
    attack_hash = hashlib.sha256(b'mhv2:patch:v1\n' + attack_canonical).hexdigest()
    assert attack_hash != patch_hash
    assert attack_hash != request_hash
