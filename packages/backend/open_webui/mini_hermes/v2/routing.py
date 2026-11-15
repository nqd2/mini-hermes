"""Deterministic Intent Routing Gate for Mini-Hermes V2 (Feature 28).

Zero-token rule-based classifier categorizing user requests into canonical document intents,
selecting initial document tools and sampling strategies, and enforcing anti-hallucination guardrails.
Operates in < 1ms with zero LLM API calls.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DocumentIntent(StrEnum):
    INSPECT_AND_OVERVIEW = "INSPECT_AND_OVERVIEW"
    PREPARE_FOR_QA = "PREPARE_FOR_QA"
    FULL_READ = "FULL_READ"
    SUMMARIZE = "SUMMARIZE"
    ANALYZE = "ANALYZE"
    SEARCH_FILTER = "SEARCH/FILTER"
    EDIT_WITH_APPROVAL = "EDIT_WITH_APPROVAL"
    PASS_THROUGH = "PASS_THROUGH"  # Conversational query without document focus


class SamplingStrategy(StrEnum):
    NONE = "none"
    SYSTEMATIC_SAMPLE = "systematic_sample"  # Head, middle, tail
    FULL_SCAN = "full_scan"                  # Permitted only for <= 200 items
    HEAD_AND_TAIL = "head_and_tail"
    INDEX_ONLY = "index_only"
    ANALYTICAL_TARGET = "analytical_target"
    SEQUENTIAL_TRAVERSAL = "sequential_traversal"


@dataclass(frozen=True, slots=True)
class IntentRoutingResult:
    intent: DocumentIntent
    confidence: float
    matched_rule: str
    initial_tool: str | None
    initial_parameters: dict[str, Any]
    sampling_strategy: SamplingStrategy
    declared_scope: str
    anti_hallucination_directives: list[str]


class DeterministicIntentRouter:
    """Regex-based multilingual context gate operating with zero LLM tokens."""

    CELL_COORD_REGEX = re.compile(r"\b([A-Z]{1,3})([1-9][0-9]{0,6})\b", re.IGNORECASE)
    CELL_RANGE_REGEX = re.compile(
        r"(?:(?:'([^']+)'|([A-Za-z0-9_]+))!)?([A-Z]{1,3}[1-9][0-9]{0,6}):([A-Z]{1,3}[1-9][0-9]{0,6})",
        re.IGNORECASE,
    )

    # Priority-ordered patterns (Vietnamese & English)
    EDIT_PATTERNS = re.compile(
        r"\b(?:sửa|thay đổi|cập nhật|thêm dòng|xóa cột|xóa dòng|đổi tên|chỉnh sửa|set cell|"
        r"đồng ý|phê duyệt|chốt|xác nhận|chấp nhận|"
        r"edit|modify|update|change|replace|insert row|delete column|delete row|rename sheet|set value|"
        r"approve|confirm|proceed|commit)\b",
        re.IGNORECASE,
    )

    ANALYZE_PATTERNS = re.compile(
        r"\b(?:phân tích|tính toán|thống kê|vẽ biểu đồ|xu hướng|độ lệch chuẩn|trung bình|"
        r"tổng doanh thu|tổng số|tính tỷ lệ|tương quan|hồi quy|tính tổng|"
        r"analyze|calculate|compute|statistics|trend|std dev|standard deviation|average|mean|"
        r"sum of|aggregate|correlation|regression)\b",
        re.IGNORECASE,
    )

    SEARCH_PATTERNS = re.compile(
        r"\b(?:tìm kiếm|tra cứu|lọc|tìm từ khóa|dòng nào có|ở đâu nói về|tìm thấy|tìm dòng|"
        r"search|filter|find|lookup|query|where does it mention|which rows contain)\b",
        re.IGNORECASE,
    )

    SUMMARIZE_PATTERNS = re.compile(
        r"\b(?:tóm tắt|sơ lược|điểm chính|tổng kết|nội dung chính|khái quát|ý chính|"
        r"summarize|summary|brief|key points|tldr|tl;dr|abstract|recap)\b",
        re.IGNORECASE,
    )

    FULL_READ_PATTERNS = re.compile(
        r"\b(?:đọc toàn bộ|đọc hết|toàn văn|in toàn bộ|toàn bộ dữ liệu|in tất cả|lấy hết|đọc hết sheet|"
        r"read full|read all|read entire|full read|dump all|everything in sheet|all text)\b",
        re.IGNORECASE,
    )

    PREPARE_QA_PATTERNS = re.compile(
        r"\b(?:chuẩn bị hỏi đáp|sẵn sàng trả lời|load tài liệu để hỏi|nạp dữ liệu|chuẩn bị câu hỏi|"
        r"prepare for qa|index document|ready for questions|process document|load into memory)\b",
        re.IGNORECASE,
    )

    INSPECT_PATTERNS = re.compile(
        r"\b(?:xem qua|tổng quan|cấu trúc|có những sheet nào|bao nhiêu trang|mục lục|danh sách sheet|"
        r"đọc file|xem file|mở file|kiểm tra file|đọc tài liệu|xem tài liệu|"
        r"overview|structure|inspect|list sheets|show outline|how many pages|describe layout|"
        r"read this|read file|open this|check this file|view file)\b",
        re.IGNORECASE,
    )

    def route(
        self,
        query: str,
        document_id: str | None = None,
        revision_no: int | None = None,
        format_type: str | None = None,
        structure_metadata: dict[str, Any] | None = None,
    ) -> IntentRoutingResult:
        """Categorizes user query into a DocumentIntent with strict anti-hallucination directives."""
        normalized = unicodedata.normalize("NFC", query).strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)

        # 1. Check for specific range references
        range_match = self.CELL_RANGE_REGEX.search(query)
        extracted_range = range_match.group(0) if range_match else None

        # 2. Priority cascade
        if self.EDIT_PATTERNS.search(normalized):
            return IntentRoutingResult(
                intent=DocumentIntent.EDIT_WITH_APPROVAL,
                confidence=1.0,
                matched_rule="EDIT_PATTERNS",
                initial_tool="propose_document_edit",
                initial_parameters={
                    "document_id": document_id,
                    "base_revision": revision_no,
                },
                sampling_strategy=SamplingStrategy.NONE,
                declared_scope="mutation_preview",
                anti_hallucination_directives=[
                    "Mutations cannot be committed directly.",
                    "Generate an edit preview and canonical patch hash only.",
                    "Inform user that preview approval expires in 15 minutes.",
                ],
            )

        if self.ANALYZE_PATTERNS.search(normalized):
            return IntentRoutingResult(
                intent=DocumentIntent.ANALYZE,
                confidence=1.0,
                matched_rule="ANALYZE_PATTERNS",
                initial_tool="execute_document_analysis",
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                    "target_range": extracted_range,
                    "goal": query,
                },
                sampling_strategy=SamplingStrategy.ANALYTICAL_TARGET,
                declared_scope=f"range:{extracted_range}" if extracted_range else "full_scope",
                anti_hallucination_directives=[
                    "Do NOT perform mental arithmetic or unverified aggregations.",
                    "Delegate all calculations to the verified analysis sandbox.",
                    "Cite exact input cell anchors in the analysis evidence manifest.",
                ],
            )

        if self.SEARCH_PATTERNS.search(normalized):
            return IntentRoutingResult(
                intent=DocumentIntent.SEARCH_FILTER,
                confidence=1.0,
                matched_rule="SEARCH_PATTERNS",
                initial_tool="search_document",
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                    "query": query,
                },
                sampling_strategy=SamplingStrategy.INDEX_ONLY,
                declared_scope="search_index",
                anti_hallucination_directives=[
                    "Matches represent retrieved chunks only.",
                    "Zero search results does not prove non-existence in unindexed sections.",
                ],
            )

        if self.SUMMARIZE_PATTERNS.search(normalized):
            return IntentRoutingResult(
                intent=DocumentIntent.SUMMARIZE,
                confidence=1.0,
                matched_rule="SUMMARIZE_PATTERNS",
                initial_tool="start_document_summary",
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                },
                sampling_strategy=SamplingStrategy.SYSTEMATIC_SAMPLE,
                declared_scope="summary_task",
                anti_hallucination_directives=[
                    "Output is a synthetic summary, NOT a verbatim transcript.",
                    "Explicitly report summary coverage, not full read coverage.",
                ],
            )

        if self.FULL_READ_PATTERNS.search(normalized):
            tool_name = "read_spreadsheet_range" if format_type == "xlsx" else "read_document_range"
            return IntentRoutingResult(
                intent=DocumentIntent.FULL_READ,
                confidence=1.0,
                matched_rule="FULL_READ_PATTERNS",
                initial_tool=tool_name,
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                    "range": extracted_range or "all",
                },
                sampling_strategy=SamplingStrategy.SEQUENTIAL_TRAVERSAL,
                declared_scope="sequential_traversal",
                anti_hallucination_directives=[
                    "Do NOT claim full-read completion until all pagination cursors are consumed.",
                    "If response reaches budget limits, mark coverage incomplete.",
                ],
            )

        if self.PREPARE_QA_PATTERNS.search(normalized):
            return IntentRoutingResult(
                intent=DocumentIntent.PREPARE_FOR_QA,
                confidence=1.0,
                matched_rule="PREPARE_QA_PATTERNS",
                initial_tool="inspect_document",
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                },
                sampling_strategy=SamplingStrategy.INDEX_ONLY,
                declared_scope="index_status",
                anti_hallucination_directives=[
                    "INDEX_COMPLETE does NOT mean LLM_CONTEXT_COMPLETE.",
                    "Confirm index readiness without loading raw file content into prompt.",
                ],
            )

        # Explicit inspect / generic file read (e.g. "đọc file Excel này") or default document fallback
        is_inspect_match = bool(self.INSPECT_PATTERNS.search(normalized))
        if is_inspect_match or document_id:
            total_rows = (structure_metadata or {}).get("total_rows", 1000)
            strategy = SamplingStrategy.FULL_SCAN if total_rows <= 200 else SamplingStrategy.SYSTEMATIC_SAMPLE
            tool_name = (
                "read_spreadsheet_range"
                if (format_type == "xlsx" and extracted_range)
                else "inspect_document"
            )
            declared_scope = (
                f"range:{extracted_range}"
                if extracted_range
                else ("full-read" if strategy == SamplingStrategy.FULL_SCAN else "sampled")
            )

            return IntentRoutingResult(
                intent=DocumentIntent.INSPECT_AND_OVERVIEW,
                confidence=1.0 if is_inspect_match else 0.9,
                matched_rule="INSPECT_PATTERNS" if is_inspect_match else "DEFAULT_INSPECT_FALLBACK",
                initial_tool=tool_name,
                initial_parameters={
                    "document_id": document_id,
                    "revision": revision_no,
                    "range": extracted_range,
                },
                sampling_strategy=strategy,
                declared_scope=declared_scope,
                anti_hallucination_directives=[
                    f"Document structure is {strategy.value}; declared scope is {declared_scope}.",
                    "Inspect all sheets/sections first and sample systematically across beginning, middle, and end.",
                    "Report actual scope (sampled, inspected, full-read) without claiming full verbatim read.",
                    "Do NOT fabricate sheet names or unobserved data; cite exact sheet and cell coordinates.",
                ],
            )

        # Conversational pass-through (no document involved)
        return IntentRoutingResult(
            intent=DocumentIntent.PASS_THROUGH,
            confidence=1.0,
            matched_rule="CONVERSATIONAL_PASS_THROUGH",
            initial_tool=None,
            initial_parameters={},
            sampling_strategy=SamplingStrategy.NONE,
            declared_scope="conversational",
            anti_hallucination_directives=[],
        )
