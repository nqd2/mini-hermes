"""Multi-State Independent Coverage Accounting Engine for Mini-Hermes V2 (Feature 29).

Implements strict validation of the orthogonal coverage domains:
  PARSER_COMPLETE != INDEX_COMPLETE != RETRIEVAL_COMPLETE != ANALYSIS_COMPLETE != SUMMARY_COMPLETE != LLM_CONTEXT_COMPLETE
alongside half-open interval union arithmetic, fragmented unit Unicode codepoint
accounting, multi-page contiguity verification, and answer evidence gating
conforming to docs/v2/phase0/05-coverage-semantics.md and coverage-proof.schema.json.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoverageType(StrEnum):
    PARSER_COVERAGE = "parser_coverage"
    INDEX_COVERAGE = "index_coverage"
    RETRIEVAL_COVERAGE = "retrieval_coverage"
    ANALYSIS_COVERAGE = "analysis_coverage"
    SUMMARY_COVERAGE = "summary_coverage"
    LLM_CONTEXT_COVERAGE = "llm_context_coverage"


class UnitType(StrEnum):
    SHEET = "sheet"
    CELL = "cell"
    ROW = "row"
    PARAGRAPH = "paragraph"
    TABLE_CELL = "table_cell"
    SECTION = "section"
    SLIDE = "slide"
    SHAPE = "shape"
    PAGE = "page"
    CHUNK = "chunk"


def _utc_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _validate_iso8601_tz(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("verified_at must be a non-empty ISO-8601 timestamp string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp for verified_at: {value}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"verified_at must be timezone-aware: {value}")


def _validate_unique_unit_ids(items: list[str], field_name: str) -> None:
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or len(item) < 1:
            raise ValueError(f"{field_name} entries must be non-empty strings")
        if item in seen:
            raise ValueError(f"{field_name} contains duplicate unit ID: {item}")
        seen.add(item)


class CoverageScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    source_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    index_build_id: str | None = Field(default=None, min_length=1)
    unit_type: str = Field(..., min_length=1)
    scope_id: str = Field(..., min_length=1)


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Interval:
        if self.start >= self.end:
            raise ValueError(f"Interval start ({self.start}) must be strictly less than end ({self.end})")
        return self


def merge_half_open_intervals(intervals: list[list[int] | tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorts, deduplicates, and merges overlapping or adjacent half-open intervals [start, end)."""
    if not intervals:
        return []
    cleaned: list[tuple[int, int]] = []
    for interval in intervals:
        if len(interval) != 2:
            raise ValueError(f"Interval must have exactly 2 elements: {interval}")
        s, e = interval[0], interval[1]
        if isinstance(s, bool) or isinstance(e, bool) or not isinstance(s, int) or not isinstance(e, int):
            raise ValueError(f"Interval bounds must be integers: {interval}")
        if s < 0 or s >= e:
            raise ValueError(f"Invalid half-open interval [{s}, {e}): requires 0 <= start < end")
        cleaned.append((s, e))

    sorted_intervals = sorted(cleaned, key=lambda x: (x[0], x[1]))
    merged: list[tuple[int, int]] = []
    curr_start, curr_end = sorted_intervals[0]
    for next_start, next_end in sorted_intervals[1:]:
        if next_start <= curr_end:
            curr_end = max(curr_end, next_end)
        else:
            merged.append((curr_start, curr_end))
            curr_start, curr_end = next_start, next_end
    merged.append((curr_start, curr_end))
    return merged


def verify_interval_union(
    intervals: list[list[int] | tuple[int, int]],
    enumerated_total: int | None,
) -> tuple[int, bool]:
    """Verifies interval bounds, merges overlaps, and returns (union_size, is_full_contiguous_cover)."""
    if enumerated_total is not None and (isinstance(enumerated_total, bool) or enumerated_total < 0):
        raise ValueError(f"enumerated_total must be non-negative integer or None, got {enumerated_total}")

    if not intervals:
        return 0, (enumerated_total == 0)

    merged = merge_half_open_intervals(intervals)
    if enumerated_total is not None:
        for s, e in merged:
            if e > enumerated_total:
                raise ValueError(
                    f"Interval [{s}, {e}) exceeds enumerated_total ({enumerated_total})"
                )

    total_covered = sum(e - s for s, e in merged)
    is_fully_contiguous = (
        enumerated_total is not None
        and len(merged) == 1
        and merged[0][0] == 0
        and merged[0][1] == enumerated_total
    )
    return total_covered, is_fully_contiguous


class CoverageProof(BaseModel):
    """Authoritative 11-field coverage proof conforming to coverage-proof.schema.json."""

    model_config = ConfigDict(extra="forbid")

    coverage_type: CoverageType
    scope: CoverageScope
    enumerated_total: int | None = Field(default=None, ge=0)
    processed_intervals: list[list[int]] = Field(default_factory=list)
    processed_count: int = Field(default=0, ge=0)
    failed_units: list[str] = Field(default_factory=list)
    unsupported_units: list[str] = Field(default_factory=list)
    excluded_units: list[str] = Field(default_factory=list)
    complete: bool = False
    proof_id: str = Field(default_factory=lambda: f"proof-{uuid.uuid4()}")
    verified_at: str = Field(default_factory=_utc_iso_z)

    @model_validator(mode="after")
    def enforce_semantic_proof_invariants(self) -> CoverageProof:
        _validate_iso8601_tz(self.verified_at)
        _validate_unique_unit_ids(self.failed_units, "failed_units")
        _validate_unique_unit_ids(self.unsupported_units, "unsupported_units")
        _validate_unique_unit_ids(self.excluded_units, "excluded_units")

        if self.complete:
            if self.enumerated_total is None:
                raise ValueError("complete=true requires a non-null enumerated_total")
            if len(self.failed_units) > 0:
                raise ValueError("complete=true requires failed_units to be empty")
            if len(self.unsupported_units) > 0:
                raise ValueError("complete=true requires unsupported_units to be empty")
            if len(self.excluded_units) > 0:
                raise ValueError("complete=true requires excluded_units to be empty")

        union_count, is_contiguous = verify_interval_union(
            self.processed_intervals, self.enumerated_total
        )
        if union_count != self.processed_count:
            raise ValueError(
                f"processed_count ({self.processed_count}) does not match interval union size ({union_count})"
            )

        if self.complete:
            if self.enumerated_total == 0:
                if len(self.processed_intervals) != 0 or self.processed_count != 0:
                    raise ValueError("Empty scope complete=true requires zero intervals and zero count")
            else:
                if not is_contiguous or union_count != self.enumerated_total:
                    raise ValueError(
                        f"complete=true requires full contiguous union [0, {self.enumerated_total}), got size {union_count}"
                    )

        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_type": self.coverage_type.value,
            "scope": self.scope.model_dump(),
            "enumerated_total": self.enumerated_total,
            "processed_intervals": [list(i) for i in self.processed_intervals],
            "processed_count": self.processed_count,
            "failed_units": list(self.failed_units),
            "unsupported_units": list(self.unsupported_units),
            "excluded_units": list(self.excluded_units),
            "complete": self.complete,
            "proof_id": self.proof_id,
            "verified_at": self.verified_at,
        }


def build_coverage_proof(
    *,
    coverage_type: CoverageType | str,
    document_id: str,
    revision: int,
    source_sha256: str,
    index_build_id: str | None,
    unit_type: str,
    scope_id: str,
    enumerated_total: int | None,
    intervals: list[list[int] | tuple[int, int]],
    failed_units: list[str] | None = None,
    unsupported_units: list[str] | None = None,
    excluded_units: list[str] | None = None,
    proof_id: str | None = None,
    verified_at: str | None = None,
) -> CoverageProof:
    """Computes interval union and constructs a verified CoverageProof."""
    failed = list(failed_units or [])
    unsupported = list(unsupported_units or [])
    excluded = list(excluded_units or [])
    merged = merge_half_open_intervals(intervals) if intervals else []
    union_count, is_contiguous = verify_interval_union(merged, enumerated_total)

    is_complete = (
        enumerated_total is not None
        and len(failed) == 0
        and len(unsupported) == 0
        and len(excluded) == 0
        and ((enumerated_total == 0 and union_count == 0) or (enumerated_total > 0 and is_contiguous))
    )

    return CoverageProof(
        coverage_type=CoverageType(coverage_type),
        scope=CoverageScope(
            document_id=str(document_id),
            revision=int(revision),
            source_sha256=source_sha256.lower(),
            index_build_id=str(index_build_id) if index_build_id else None,
            unit_type=unit_type,
            scope_id=scope_id,
        ),
        enumerated_total=enumerated_total,
        processed_intervals=[[s, e] for s, e in merged],
        processed_count=union_count,
        failed_units=failed,
        unsupported_units=unsupported,
        excluded_units=excluded,
        complete=is_complete,
        proof_id=proof_id or f"proof-{uuid.uuid4()}",
        verified_at=verified_at or _utc_iso_z(),
    )


# =============================================================================
# Fragmented Units & Codepoint Coverage Ledger
# =============================================================================


@dataclass(frozen=True, slots=True)
class FragmentScopeKey:
    document_id: str
    revision: int
    source_sha256: str
    index_build_id: str | None
    unit_id: str
    representation: str
    consumer: str
    request_scope: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FragmentScopeKey:
        return cls(
            document_id=str(data.get("document_id") or data.get("document") or ""),
            revision=int(data.get("revision") or 0),
            source_sha256=str(data.get("source_sha256") or data.get("checksum") or "").lower(),
            index_build_id=(
                str(data.get("index_build_id") or data.get("build"))
                if (data.get("index_build_id") or data.get("build")) is not None
                else None
            ),
            unit_id=str(data.get("unit_id") or data.get("unit") or ""),
            representation=str(data.get("representation") or "default"),
            consumer=str(data.get("consumer") or "default"),
            request_scope=str(data.get("request_scope") or data.get("scope") or "default"),
        )


@dataclass
class FragmentState:
    scope: FragmentScopeKey
    total_codepoints: int | None
    required_fields: set[str]
    observed_fields: set[str] = field(default_factory=set)
    intervals: list[tuple[int, int]] = field(default_factory=list)
    empty_observed: bool = False
    empty_evidence: str | None = None
    invalidated: bool = False

    def is_complete(self) -> bool:
        if self.invalidated:
            return False
        if self.total_codepoints is None or self.total_codepoints < 0:
            return False
        if not self.required_fields.issubset(self.observed_fields):
            return False
        if self.total_codepoints == 0:
            return self.empty_observed and bool(self.empty_evidence) and len(self.intervals) == 0

        if not self.intervals:
            return False
        sorted_ints = sorted(self.intervals)
        curr_end = 0
        for s, e in sorted_ints:
            if not (0 <= s < e <= self.total_codepoints):
                return False
            if s > curr_end:
                return False
            curr_end = max(curr_end, e)
        return curr_end == self.total_codepoints


class FragmentCoverageLedger:
    """Tracks codepoint fragments of oversized units across paginated responses."""

    def __init__(self) -> None:
        self._states: dict[FragmentScopeKey, FragmentState] = {}

    def record_fragment(
        self,
        scope: FragmentScopeKey,
        start: int,
        end: int,
        evidence_id: str | None,
        total_codepoints: int | None,
        required_fields: list[str] | set[str],
        observed_fields: list[str] | set[str],
        fragment_scope: FragmentScopeKey | None = None,
    ) -> bool:
        """Records a verified text fragment measured in Unicode codepoints."""
        state = self._states.get(scope)
        if state is None:
            state = FragmentState(
                scope=scope,
                total_codepoints=total_codepoints,
                required_fields=set(required_fields),
                observed_fields=set(observed_fields),
            )
            self._states[scope] = state
        else:
            if state.total_codepoints != total_codepoints:
                state.invalidated = True
                return False
            state.required_fields.update(required_fields)
            state.observed_fields.update(observed_fields)

        if fragment_scope is not None and fragment_scope != scope:
            state.invalidated = True
            return False
        if not evidence_id:
            state.invalidated = True
            return False
        if total_codepoints is None or total_codepoints < 0:
            state.invalidated = True
            return False
        if not (0 <= start < end <= total_codepoints):
            state.invalidated = True
            return False

        state.intervals.append((start, end))
        return state.is_complete()

    def record_empty(
        self,
        scope: FragmentScopeKey,
        evidence_id: str | None,
        required_fields: list[str] | set[str],
        observed_fields: list[str] | set[str],
        *,
        empty_observed: bool = True,
    ) -> bool:
        """Explicitly records observation of a zero-codepoint unit."""
        state = FragmentState(
            scope=scope,
            total_codepoints=0,
            required_fields=set(required_fields),
            observed_fields=set(observed_fields),
            empty_observed=bool(empty_observed),
            empty_evidence=evidence_id if evidence_id else None,
        )
        self._states[scope] = state
        return state.is_complete()

    def is_unit_complete(self, scope: FragmentScopeKey) -> bool:
        state = self._states.get(scope)
        return state.is_complete() if state else False


def evaluate_fragment_coverage_case(case: dict[str, Any]) -> bool:
    """Evaluates a fragment coverage case (compatible with Phase 0 fragment-coverage-vectors.json)."""
    total = case.get("total")
    if total is None or (isinstance(total, int) and total < 0):
        return False

    required_fields = set(case.get("required_fields") or [])
    observed_fields = set(case.get("observed_fields") or [])
    if not required_fields.issubset(observed_fields):
        return False

    expected_scope = FragmentScopeKey.from_dict(case["scope"])
    fragments = case.get("fragments") or []

    ledger = FragmentCoverageLedger()
    if total == 0:
        if fragments:
            return False
        return ledger.record_empty(
            scope=expected_scope,
            evidence_id=case.get("empty_evidence"),
            required_fields=required_fields,
            observed_fields=observed_fields,
            empty_observed=bool(case.get("empty_observed", False)),
        )

    for frag in fragments:
        frag_scope = FragmentScopeKey.from_dict(frag.get("scope") or {})
        ledger.record_fragment(
            scope=expected_scope,
            start=frag.get("start", -1),
            end=frag.get("end", -1),
            evidence_id=frag.get("evidence"),
            total_codepoints=total,
            required_fields=required_fields,
            observed_fields=observed_fields,
            fragment_scope=frag_scope,
        )
    return ledger.is_unit_complete(expected_scope)


# =============================================================================
# Evidence & Answer Gate Models
# =============================================================================


class RevisionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    source_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")


class IndexRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_id: str = Field(..., min_length=1)
    parser_version: str = Field(default="v2.0", min_length=1)
    chunker_version: str = Field(default="v2.0", min_length=1)
    schema_version: str = Field(default="v2.0", min_length=1)


class AnchorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1)
    document_id: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    source_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    location: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(..., min_length=1)
    locator: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    revision_ref: RevisionRef | None = None
    index_ref: IndexRef | None = None
    anchors: list[AnchorRef] = Field(default_factory=list)


class AnswerEvidence(BaseModel):
    """Binds an assistant answer claim to verified anchors and explicit proven unit scope."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    source_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    index_build_id: str | None = None
    claimed_scope: str = Field(..., min_length=1)  # e.g. "sampled", "filtered", "full_workbook", "range:Sheet1!A1:B10"
    claimed_complete: bool = False
    citations: list[EvidenceRef] = Field(default_factory=list)
    proven_unit_ordinals: list[int] = Field(default_factory=list)


# =============================================================================
# Multi-Page Retrieval Contiguity & Multi-State Coverage Tracker
# =============================================================================


class MultiPageCoverageAccumulator:
    """Accumulates paginated retrieval slices and verifies contiguous coverage on a pinned build."""

    def __init__(
        self,
        *,
        document_id: str,
        revision: int,
        source_sha256: str,
        index_build_id: str,
        unit_type: str,
        scope_id: str,
        enumerated_total: int | None,
        failed_units: list[str] | None = None,
        unsupported_units: list[str] | None = None,
        excluded_units: list[str] | None = None,
    ) -> None:
        self.document_id = str(document_id)
        self.revision = int(revision)
        self.source_sha256 = source_sha256.lower()
        self.index_build_id = str(index_build_id)
        self.unit_type = unit_type
        self.scope_id = scope_id
        self.enumerated_total = enumerated_total
        self.failed_units = list(failed_units or [])
        self.unsupported_units = list(unsupported_units or [])
        self.excluded_units = list(excluded_units or [])
        self._whole_unit_intervals: list[tuple[int, int]] = []
        self._fragment_ledger = FragmentCoverageLedger()

    def _verify_binding(
        self,
        *,
        document_id: str,
        revision: int,
        source_sha256: str,
        index_build_id: str,
        scope_id: str | None = None,
    ) -> None:
        if (
            str(document_id) != self.document_id
            or int(revision) != self.revision
            or source_sha256.lower() != self.source_sha256
            or str(index_build_id) != self.index_build_id
            or (scope_id is not None and scope_id != self.scope_id)
        ):
            raise ValueError(
                "Cross-revision, cross-build, or cross-scope page cannot be combined into retrieval coverage"
            )

    def add_whole_unit_interval(
        self,
        start_ordinal: int,
        end_ordinal: int,
        *,
        document_id: str,
        revision: int,
        source_sha256: str,
        index_build_id: str,
        scope_id: str | None = None,
    ) -> None:
        """Records a half-open ordinal interval [start_ordinal, end_ordinal) of whole completed units."""
        self._verify_binding(
            document_id=document_id,
            revision=revision,
            source_sha256=source_sha256,
            index_build_id=index_build_id,
            scope_id=scope_id,
        )
        if start_ordinal < end_ordinal:
            if self.enumerated_total is not None and (
                start_ordinal < 0 or end_ordinal > self.enumerated_total
            ):
                raise ValueError(
                    f"Unit interval [{start_ordinal}, {end_ordinal}) out of bounds for total {self.enumerated_total}"
                )
            self._whole_unit_intervals.append((start_ordinal, end_ordinal))

    def add_unit_fragment(
        self,
        *,
        item_ordinal: int,
        unit_id: str,
        start_codepoint: int,
        end_codepoint: int,
        total_codepoints: int,
        evidence_id: str,
        document_id: str,
        revision: int,
        source_sha256: str,
        index_build_id: str,
        required_fields: list[str] | None = None,
        observed_fields: list[str] | None = None,
        consumer: str = "default",
    ) -> bool:
        """Records a codepoint fragment for an oversized unit.

        Credits the unit ordinal [item_ordinal, item_ordinal + 1) to whole-unit intervals
        ONLY once all fragments [0, total_codepoints) and required fields are verified.
        """
        self._verify_binding(
            document_id=document_id,
            revision=revision,
            source_sha256=source_sha256,
            index_build_id=index_build_id,
        )
        key = FragmentScopeKey(
            document_id=self.document_id,
            revision=self.revision,
            source_sha256=self.source_sha256,
            index_build_id=self.index_build_id,
            unit_id=unit_id,
            representation="v2",
            consumer=consumer,
            request_scope=self.scope_id,
        )
        req_f = required_fields if required_fields is not None else ["raw_value"]
        obs_f = observed_fields if observed_fields is not None else ["raw_value"]
        completed = self._fragment_ledger.record_fragment(
            scope=key,
            start=start_codepoint,
            end=end_codepoint,
            evidence_id=evidence_id,
            total_codepoints=total_codepoints,
            required_fields=req_f,
            observed_fields=obs_f,
        )
        if completed:
            self._whole_unit_intervals.append((item_ordinal, item_ordinal + 1))
        return completed

    def build_proof(self, coverage_type: CoverageType = CoverageType.RETRIEVAL_COVERAGE) -> CoverageProof:
        return build_coverage_proof(
            coverage_type=coverage_type,
            document_id=self.document_id,
            revision=self.revision,
            source_sha256=self.source_sha256,
            index_build_id=self.index_build_id,
            unit_type=self.unit_type,
            scope_id=self.scope_id,
            enumerated_total=self.enumerated_total,
            intervals=self._whole_unit_intervals,
            failed_units=self.failed_units,
            unsupported_units=self.unsupported_units,
            excluded_units=self.excluded_units,
        )


class MultiStateCoverageTracker:
    """Tracks independent coverage states and answer evidence for a document revision.

    Enforces PARSER_COMPLETE != INDEX_COMPLETE != RETRIEVAL_COMPLETE != ANALYSIS_COMPLETE != SUMMARY_COMPLETE != LLM_CONTEXT_COMPLETE.
    Completing one domain NEVER sets or implies completion of any other domain.
    """

    def __init__(
        self,
        *,
        document_id: str,
        revision: int,
        source_sha256: str,
        index_build_id: str | None = None,
    ) -> None:
        self.document_id = str(document_id)
        self.revision = int(revision)
        self.source_sha256 = source_sha256.lower()
        self.index_build_id = str(index_build_id) if index_build_id else None
        self._proofs: dict[CoverageType, CoverageProof] = {}
        self._answer_evidence: AnswerEvidence | None = None

    def record_proof(self, proof: CoverageProof) -> None:
        if (
            proof.scope.document_id != self.document_id
            or proof.scope.revision != self.revision
            or proof.scope.source_sha256 != self.source_sha256
        ):
            raise ValueError("CoverageProof scope does not match tracker document/revision/checksum")
        if (
            self.index_build_id is not None
            and proof.scope.index_build_id is not None
            and proof.scope.index_build_id != self.index_build_id
        ):
            raise ValueError("CoverageProof index_build_id does not match tracker build_id")
        self._proofs[proof.coverage_type] = proof

    def get_proof(self, coverage_type: CoverageType | str) -> CoverageProof | None:
        return self._proofs.get(CoverageType(coverage_type))

    def record_llm_context_from_retrieval(
        self,
        retrieval_proof: CoverageProof,
        *,
        admitted_intervals: list[list[int] | tuple[int, int]],
        compacted_or_truncated: bool = False,
    ) -> CoverageProof:
        """Computes independent LLM_CONTEXT_COVERAGE from what actually survived context budget compaction."""
        if (
            retrieval_proof.scope.document_id != self.document_id
            or retrieval_proof.scope.revision != self.revision
            or retrieval_proof.scope.source_sha256 != self.source_sha256
        ):
            raise ValueError("Retrieval proof scope mismatch")

        excluded = list(retrieval_proof.excluded_units)
        if compacted_or_truncated and "context_compacted" not in excluded:
            excluded.append("context_compacted")

        llm_proof = build_coverage_proof(
            coverage_type=CoverageType.LLM_CONTEXT_COVERAGE,
            document_id=self.document_id,
            revision=self.revision,
            source_sha256=self.source_sha256,
            index_build_id=retrieval_proof.scope.index_build_id,
            unit_type=retrieval_proof.scope.unit_type,
            scope_id=retrieval_proof.scope.scope_id,
            enumerated_total=retrieval_proof.enumerated_total,
            intervals=admitted_intervals,
            failed_units=retrieval_proof.failed_units,
            unsupported_units=retrieval_proof.unsupported_units,
            excluded_units=excluded,
        )
        self._proofs[CoverageType.LLM_CONTEXT_COVERAGE] = llm_proof
        return llm_proof

    def record_answer_evidence(self, evidence: AnswerEvidence) -> None:
        """Validates and records answer evidence against proven coverage domains."""
        self.verify_answer_claim(evidence)
        self._answer_evidence = evidence

    def verify_answer_claim(
        self,
        evidence: AnswerEvidence,
        *,
        required_domain: CoverageType | None = None,
    ) -> bool:
        """Enforces that an answer does not overclaim completeness or cite out-of-scope anchors."""
        if (
            evidence.document_id != self.document_id
            or evidence.revision != self.revision
            or evidence.source_sha256.lower() != self.source_sha256
        ):
            raise ValueError("AnswerEvidence revision identity mismatch")

        for citation in evidence.citations:
            if citation.revision != self.revision:
                raise ValueError(
                    f"Citation {citation.evidence_id} references revision {citation.revision} != {self.revision}"
                )
            for anchor in citation.anchors:
                if (
                    anchor.document_id != self.document_id
                    or anchor.revision != self.revision
                    or anchor.source_sha256.lower() != self.source_sha256
                ):
                    raise ValueError(f"Out-of-scope anchor in citation {citation.evidence_id}")

        # Filter/sample answers must never advertise FULL_WORKBOOK
        if evidence.claimed_scope.upper() == "FULL_WORKBOOK":
            retrieval = self._proofs.get(CoverageType.RETRIEVAL_COVERAGE)
            analysis = self._proofs.get(CoverageType.ANALYSIS_COVERAGE)
            llm_ctx = self._proofs.get(CoverageType.LLM_CONTEXT_COVERAGE)
            if not (
                (retrieval and retrieval.complete)
                or (analysis and analysis.complete)
                or (llm_ctx and llm_ctx.complete)
            ):
                raise ValueError(
                    "Cannot advertise FULL_WORKBOOK when neither retrieval, analysis, nor LLM context is complete"
                )

        if evidence.claimed_complete:
            if required_domain is not None:
                domain_proof = self._proofs.get(required_domain)
                if domain_proof is None or not domain_proof.complete:
                    raise ValueError(
                        f"Answer claims complete=true but required domain {required_domain.value} is not complete"
                    )
            else:
                # Parsing or indexing alone NEVER justifies an answer claiming complete reading/analysis
                retrieval = self._proofs.get(CoverageType.RETRIEVAL_COVERAGE)
                analysis = self._proofs.get(CoverageType.ANALYSIS_COVERAGE)
                llm_ctx = self._proofs.get(CoverageType.LLM_CONTEXT_COVERAGE)
                if not (
                    (retrieval and retrieval.complete)
                    or (analysis and analysis.complete)
                    or (llm_ctx and llm_ctx.complete)
                ):
                    raise ValueError(
                        "Answer cannot claim complete=true based only on parser/index/summary states"
                    )
        return True

    def get_status_matrix(self) -> dict[str, Any]:
        """Returns independent completion status across all coverage domains and answer evidence."""
        def _is_done(ct: CoverageType) -> bool:
            p = self._proofs.get(ct)
            return bool(p.complete) if p is not None else False

        return {
            "parser_complete": _is_done(CoverageType.PARSER_COVERAGE),
            "index_complete": _is_done(CoverageType.INDEX_COVERAGE),
            "retrieval_complete": _is_done(CoverageType.RETRIEVAL_COVERAGE),
            "analysis_complete": _is_done(CoverageType.ANALYSIS_COVERAGE),
            "summary_complete": _is_done(CoverageType.SUMMARY_COVERAGE),
            "llm_context_complete": _is_done(CoverageType.LLM_CONTEXT_COVERAGE),
            "answer_evidence_verified": self._answer_evidence is not None,
            "answer_claimed_scope": self._answer_evidence.claimed_scope if self._answer_evidence else None,
        }


# Alias for compatibility with Explorer 2 blueprint
MultiStateCoverageCoordinator = MultiStateCoverageTracker
