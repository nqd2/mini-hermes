"""Phase 0 evidence: fixture integrity and current provider request boundaries.

These tests characterize V1 and fixture contents; they do not assert V2 behavior.
"""

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from weai.agent_runtime.context import ContextManager, PreparedContext
from weai.agent_runtime.gateway import ProviderGateway
from weai.agent_runtime.request_budget import RequestBudgetLimits, RequestBudgetManager
from weai.documents.chunks import extract_structured_text
from weai.utils.json_codec import JSONCodec


FIXTURES = Path(__file__).parent / 'fixtures' / 'v2'


def test_xlsx_fixture_checksums_match_manifest():
    for line in (FIXTURES / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, filename = line.split(maxsplit=1)
        assert sha256((FIXTURES / filename).read_bytes()).hexdigest() == digest


def test_xlsx_fixture_covers_hidden_sheet_and_absent_formula_cache():
    path = FIXTURES / 'xlsx-f01-f03-hidden-missing-cache.xlsx'
    formulas = load_workbook(path, data_only=False)
    cached = load_workbook(path, data_only=True)
    try:
        assert formulas['Hidden'].sheet_state == 'hidden'
        assert formulas['Visible']['B2'].value == '=A2*2'
        assert cached['Visible']['B2'].value is None
    finally:
        formulas.close()
        cached.close()


def test_xlsx_large_fixture_has_multiline_merged_and_cached_formulas():
    path = FIXTURES / 'xlsx-f05-synthetic.xlsx'
    formulas = load_workbook(path, data_only=False)
    cached = load_workbook(path, data_only=True)
    try:
        cells = [cell for sheet in formulas for row in sheet for cell in row if cell.value is not None]
        assert len(formulas.sheetnames) >= 3
        assert sum(len(str(cell.value)) for cell in cells) > 57_765
        assert any(isinstance(cell.value, str) and '\n' in cell.value for cell in cells)
        assert any(sheet.merged_cells.ranges for sheet in formulas)
        assert any(cell.data_type == 'f' and cached[cell.parent.title][cell.coordinate].value is not None for cell in cells)
    finally:
        formulas.close()
        cached.close()


def test_preparation_limit_accepts_95000_and_rejects_95001_characters():
    manager = ContextManager(request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_bytes=400_000)))
    payload = {'model': 'large-context-model', 'messages': [{'role': 'user', 'content': ''}]}
    overhead = len(JSONCodec.dumps(payload))
    payload['messages'][0]['content'] = 'x' * (95_000 - overhead)
    options = {'context_window_tokens': 100_000, 'max_output_tokens': 1_000, 'tool_calling': True}

    prepared = manager.prepare(payload, 'https://provider.invalid/v1', options)
    assert prepared.metrics['serialized_chars'] == 95_000
    payload['messages'][0]['content'] += 'x'
    with pytest.raises(HTTPException) as error:
        manager.prepare(payload, 'https://provider.invalid/v1', options)
    assert error.value.detail['code'] == 'request_preparation_char_limit_exceeded'
    assert error.value.detail['serialized_chars'] == 95_001


@pytest.mark.asyncio
async def test_gateway_rechecks_wire_budget_before_any_provider_request():
    gateway = ProviderGateway(request_budget=RequestBudgetManager(RequestBudgetLimits(max_json_bytes=400_000)))
    wire = JSONCodec.dumps({'model': 'small', 'messages': [], 'padding': 'x' * 100_000})
    prepared = PreparedContext(payload={}, wire_body=wire, metrics={})

    class Session:
        called = False

        async def request(self, *args, **kwargs):
            self.called = True

    session = Session()
    with pytest.raises(HTTPException) as error:
        await gateway.send(session, 'POST', 'https://provider.invalid/v1/chat/completions', prepared)
    assert error.value.detail['code'] == 'request_json_char_limit_exceeded'
    assert session.called is False


def test_exact_length_xlsx_fixture_has_expected_structures():
    path = FIXTURES / 'xlsx-f05-exact-57765.xlsx'
    workbook = load_workbook(path, data_only=False)
    try:
        assert workbook.sheetnames == ['Interview', 'Metrics', 'Hidden']
        assert workbook['Hidden'].sheet_state == 'hidden'
        assert '\n' in workbook['Interview']['B2'].value
        assert 'A5:B5' in {str(item) for item in workbook['Interview'].merged_cells.ranges}
        assert workbook['Metrics']['B3'].value == '=B2*2'
        assert len(extract_structured_text(path.read_bytes(), path.name)) == 57_765
    finally:
        workbook.close()
