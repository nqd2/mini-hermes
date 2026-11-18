"""Milestone M3.2 & M3.3 Tests: Deterministic Intent Routing Gate and Multi-State Coverage Accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weai.persistence.v2.coverage import (
    AnchorRef,
    AnswerEvidence,
    CoverageProof,
    CoverageScope,
    CoverageType,
    EvidenceRef,
    MultiPageCoverageAccumulator,
    MultiStateCoverageTracker,
    build_coverage_proof,
    evaluate_fragment_coverage_case,
    merge_half_open_intervals,
)
from weai.persistence.v2.routing import (
    DeterministicIntentRouter,
    DocumentIntent,
    SamplingStrategy,
)


def test_deterministic_intent_router_all_7_intents_en_and_vi():
    router = DeterministicIntentRouter()

    # 1. Generic "đọc file Excel này" -> INSPECT_AND_OVERVIEW, systematic sampling, actual scope reporting
    res_vi_excel = router.route(
        "đọc file Excel này giúp tôi",
        document_id="doc-xlsx-1",
        revision_no=1,
        format_type="xlsx",
        structure_metadata={"total_rows": 1500},
    )
    assert res_vi_excel.intent == DocumentIntent.INSPECT_AND_OVERVIEW
    assert res_vi_excel.initial_tool == "inspect_document"
    assert res_vi_excel.sampling_strategy == SamplingStrategy.SYSTEMATIC_SAMPLE
    assert res_vi_excel.declared_scope == "sampled"
    assert any("without claiming full verbatim read" in d for d in res_vi_excel.anti_hallucination_directives)
    assert any("sample systematically" in d for d in res_vi_excel.anti_hallucination_directives)

    # Small Excel file (<= 200 rows) -> FULL_SCAN allowed
    res_small_excel = router.route(
        "Read this file",
        document_id="doc-xlsx-small",
        revision_no=1,
        format_type="xlsx",
        structure_metadata={"total_rows": 120},
    )
    assert res_small_excel.intent == DocumentIntent.INSPECT_AND_OVERVIEW
    assert res_small_excel.sampling_strategy == SamplingStrategy.FULL_SCAN
    assert res_small_excel.declared_scope == "full-read"

    # 2. PREPARE_FOR_QA (VI + EN)
    for q in ("Chuẩn bị hỏi đáp cho tài liệu này", "Please prepare for QA and index document"):
        res = router.route(q, document_id="doc-1", revision_no=1)
        assert res.intent == DocumentIntent.PREPARE_FOR_QA
        assert res.initial_tool == "inspect_document"
        assert res.sampling_strategy == SamplingStrategy.INDEX_ONLY

    # 3. FULL_READ (VI + EN)
    for q in ("Đọc toàn bộ dữ liệu trong bảng", "Please read all text in the document"):
        res = router.route(q, document_id="doc-1", revision_no=1, format_type="xlsx")
        assert res.intent == DocumentIntent.FULL_READ
        assert res.initial_tool == "read_spreadsheet_range"
        assert res.sampling_strategy == SamplingStrategy.SEQUENTIAL_TRAVERSAL

    # 4. SUMMARIZE (VI + EN)
    for q in ("Tóm tắt các điểm chính của báo cáo", "Give me an executive summary and key points"):
        res = router.route(q, document_id="doc-1", revision_no=1)
        assert res.intent == DocumentIntent.SUMMARIZE
        assert res.initial_tool == "start_document_summary"
        assert res.sampling_strategy == SamplingStrategy.SYSTEMATIC_SAMPLE

    # 5. ANALYZE (VI + EN, with range extraction)
    res_analyze_vi = router.route(
        "Phân tích xu hướng và tính tổng doanh thu vùng Revenue!B2:B500",
        document_id="doc-1",
        revision_no=2,
        format_type="xlsx",
    )
    assert res_analyze_vi.intent == DocumentIntent.ANALYZE
    assert res_analyze_vi.initial_tool == "execute_document_analysis"
    assert res_analyze_vi.initial_parameters["target_range"] == "Revenue!B2:B500"
    assert res_analyze_vi.declared_scope == "range:Revenue!B2:B500"

    res_analyze_en = router.route(
        "Calculate the standard deviation and correlation of quarterly revenue",
        document_id="doc-1",
        revision_no=2,
    )
    assert res_analyze_en.intent == DocumentIntent.ANALYZE
    assert res_analyze_en.initial_tool == "execute_document_analysis"

    # 6. SEARCH_FILTER (SEARCH/FILTER) (VI + EN)
    for q in ("Tìm kiếm dòng nào có hợp đồng ABC-2026", "Search and filter rows where status is pending"):
        res = router.route(q, document_id="doc-1", revision_no=1)
        assert res.intent == DocumentIntent.SEARCH_FILTER
        assert res.intent.value == "SEARCH/FILTER"
        assert res.initial_tool == "search_document"
        assert res.sampling_strategy == SamplingStrategy.INDEX_ONLY

    # 7. EDIT_WITH_APPROVAL (VI + EN)
    for q in ("Cập nhật giá trị ô A5 thành 5000", "Modify the sheet and insert row at top"):
        res = router.route(q, document_id="doc-1", revision_no=3)
        assert res.intent == DocumentIntent.EDIT_WITH_APPROVAL
        assert res.initial_tool == "propose_document_edit"
        assert res.declared_scope == "mutation_preview"
        assert any("15 minutes" in d for d in res.anti_hallucination_directives)

    # 8. Conversational PASS_THROUGH when no document is referenced
    res_chat = router.route("Xin chào, hôm nay thời tiết thế nào?", document_id=None)
    assert res_chat.intent == DocumentIntent.PASS_THROUGH
    assert res_chat.initial_tool is None


def test_phase0_fragment_coverage_vectors_all_18_cases():
    repo_root = Path(__file__).resolve().parents[3]
    vectors_path = repo_root / "docs" / "v2" / "phase0" / "fixtures" / "fragment-coverage-vectors.json"
    data = json.loads(vectors_path.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) == 18

    for case in cases:
        actual = evaluate_fragment_coverage_case(case)
        assert actual is case["expected"], (
            f"Vector {case['name']} failed: expected {case['expected']}, got {actual}"
        )


def test_multi_page_coverage_accumulator_contiguity_gaps_fragments_and_cross_build_rejection():
    sha = "a" * 64
    acc = MultiPageCoverageAccumulator(
        document_id="doc-100",
        revision=1,
        source_sha256=sha,
        index_build_id="build-1",
        unit_type="cell",
        scope_id="Sheet1!A1:A10",
        enumerated_total=10,
    )

    # Page 1: whole units [0, 4)
    acc.add_whole_unit_interval(
        0, 4, document_id="doc-100", revision=1, source_sha256=sha, index_build_id="build-1"
    )
    # Page 2: skip unit 4 (oversized cell being fragmented), add [5, 10)
    acc.add_whole_unit_interval(
        5, 10, document_id="doc-100", revision=1, source_sha256=sha, index_build_id="build-1"
    )
    # Gap at ordinal 4 -> complete must be False!
    proof_with_gap = acc.build_proof()
    assert proof_with_gap.complete is False
    assert proof_with_gap.processed_count == 9
    assert proof_with_gap.processed_intervals == [[0, 4], [5, 10]]

    # Now feed fragments for ordinal 4 (total_codepoints=1000)
    done_part_1 = acc.add_unit_fragment(
        item_ordinal=4,
        unit_id="Sheet1!A5",
        start_codepoint=0,
        end_codepoint=600,
        total_codepoints=1000,
        evidence_id="ev-frag-1",
        document_id="doc-100",
        revision=1,
        source_sha256=sha,
        index_build_id="build-1",
    )
    assert done_part_1 is False
    assert acc.build_proof().complete is False

    # Feed final fragment [600, 1000) -> completes unit 4 and bridges [0, 4) + [4, 5) + [5, 10) into [0, 10)!
    done_part_2 = acc.add_unit_fragment(
        item_ordinal=4,
        unit_id="Sheet1!A5",
        start_codepoint=600,
        end_codepoint=1000,
        total_codepoints=1000,
        evidence_id="ev-frag-2",
        document_id="doc-100",
        revision=1,
        source_sha256=sha,
        index_build_id="build-1",
    )
    assert done_part_2 is True
    full_proof = acc.build_proof()
    assert full_proof.complete is True
    assert full_proof.processed_count == 10
    assert full_proof.processed_intervals == [[0, 10]]

    # Cross-build or cross-revision page must be rejected
    with pytest.raises(ValueError, match="Cross-revision, cross-build"):
        acc.add_whole_unit_interval(
            0, 2, document_id="doc-100", revision=1, source_sha256=sha, index_build_id="build-2"
        )


def test_multi_state_coverage_independence_and_answer_evidence_overclaim_gating():
    sha = "b" * 64
    tracker = MultiStateCoverageTracker(
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
    )

    # 1. Complete PARSER_COVERAGE and INDEX_COVERAGE (100/100 units)
    parser_proof = build_coverage_proof(
        coverage_type=CoverageType.PARSER_COVERAGE,
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
        unit_type="row",
        scope_id="workbook",
        enumerated_total=100,
        intervals=[[0, 100]],
    )
    index_proof = build_coverage_proof(
        coverage_type=CoverageType.INDEX_COVERAGE,
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
        unit_type="row",
        scope_id="workbook",
        enumerated_total=100,
        intervals=[[0, 100]],
    )
    tracker.record_proof(parser_proof)
    tracker.record_proof(index_proof)

    matrix = tracker.get_status_matrix()
    assert matrix["parser_complete"] is True
    assert matrix["index_complete"] is True
    # PARSER_COMPLETE != INDEX_COMPLETE != RETRIEVAL_COMPLETE != ANALYSIS_COMPLETE != SUMMARY_COMPLETE != LLM_CONTEXT_COMPLETE
    assert matrix["retrieval_complete"] is False
    assert matrix["analysis_complete"] is False
    assert matrix["summary_complete"] is False
    assert matrix["llm_context_complete"] is False

    # 2. Attempting to claim FULL_WORKBOOK or claimed_complete=True when only parser & index are complete MUST FAIL
    overclaim = AnswerEvidence(
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
        claimed_scope="FULL_WORKBOOK",
        claimed_complete=True,
        citations=[
            EvidenceRef(
                evidence_id="ev-1",
                locator="xlsx://doc-200/sheets/Sheet1/cells/A1:B10",
                revision=3,
            )
        ],
    )
    with pytest.raises(ValueError, match="Cannot advertise FULL_WORKBOOK"):
        tracker.record_answer_evidence(overclaim)

    # 3. Record complete RETRIEVAL_COVERAGE [0, 100), but compact LLM_CONTEXT_COVERAGE to [40, 100)
    retrieval_proof = build_coverage_proof(
        coverage_type=CoverageType.RETRIEVAL_COVERAGE,
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
        unit_type="row",
        scope_id="workbook",
        enumerated_total=100,
        intervals=[[0, 100]],
    )
    tracker.record_proof(retrieval_proof)
    llm_proof = tracker.record_llm_context_from_retrieval(
        retrieval_proof,
        admitted_intervals=[[40, 100]],
        compacted_or_truncated=True,
    )
    assert retrieval_proof.complete is True
    assert llm_proof.complete is False
    assert "context_compacted" in llm_proof.excluded_units

    matrix2 = tracker.get_status_matrix()
    assert matrix2["retrieval_complete"] is True
    assert matrix2["llm_context_complete"] is False

    # Requiring LLM_CONTEXT_COVERAGE for a complete claim must fail because context was compacted
    with pytest.raises(ValueError, match="llm_context_coverage is not complete"):
        tracker.verify_answer_claim(overclaim, required_domain=CoverageType.LLM_CONTEXT_COVERAGE)

    # Honest sampled/scoped claim with matching revision citation succeeds
    honest_evidence = AnswerEvidence(
        document_id="doc-200",
        revision=3,
        source_sha256=sha,
        index_build_id="build-300",
        claimed_scope="sampled",
        claimed_complete=False,
        citations=[
            EvidenceRef(
                evidence_id="ev-honest",
                locator="xlsx://doc-200/sheets/Sheet1/cells/A41:B100",
                revision=3,
                anchors=[
                    AnchorRef(
                        kind="cell_range",
                        document_id="doc-200",
                        revision=3,
                        source_sha256=sha,
                        location={"sheet": "Sheet1", "range": "A41:B100"},
                    )
                ],
            )
        ],
        proven_unit_ordinals=list(range(40, 100)),
    )
    tracker.record_answer_evidence(honest_evidence)
    assert tracker.get_status_matrix()["answer_evidence_verified"] is True
    assert tracker.get_status_matrix()["answer_claimed_scope"] == "sampled"
