# V2 Coverage Semantics — Evidence Before Claims

**Status:** Proposed formal semantics; requires parser-specific proof tests.

## Coverage domains are not interchangeable

- **`parser_coverage`**: portion of enumerated source units parse-attempted, with successes/failures/unsupported units; text-layer extraction of PDF does not certify embedded images.
- **`index_coverage`**: portion of parsed canonical units represented in the validated active index, including index failures.
- **`retrieval_coverage`**: portion of source units returned to a specific tool consumer for a specific request/snapshot. Index presence does not imply retrieval.
- **`analysis_coverage`**: units actually consumed by a verified analysis execution; result includes program/job/provenance and exclusions.
- **`summary_coverage`**: units consumed by summary pipeline with task provenance, not proof of verbatim LLM exposure.
- **`llm_context_coverage`**: units/material actually serialized into LLM context and admitted inside provider request budget.
- **`answer_evidence`**: anchors supporting claims, with completeness claim restricted to specifically proven unit set.

`PARSER_COMPLETE != INDEX_COMPLETE != ANALYSIS_COMPLETE != LLM_CONTEXT_COMPLETE`.

## Canonical denominator and unit policy

Every coverage statement MUST include `coverage_type`; a `scope` containing `document_id`, numeric `revision`, `source_sha256`, nullable `index_build_id`, `unit_type` and `scope_id`; plus `enumerated_total`, `processed_intervals`, `processed_count`, `failed_units`, `unsupported_units`, `excluded_units`, `complete`, `proof_id` and `verified_at`. The [proposed JSON Schema](schemas/coverage-proof.schema.json) defines the structural shape; it is not a substitute for the semantic proof checks below.

Choose domain-appropriate enumerated units: XLSX cells/rows per sheet; DOCX ordered blocks/paragraphs/tables; PPTX slide/shape/text-run identifiers; PDF pages/text spans with explicit unsupported image/nontext flags. Denominators must not silently drop hidden sheets or blank cells; define whether blank, sparse and out-of-range cells count. A total of zero must be distinguished from unknown count.

## Proposed R1 parser completeness rules for review

These rules define a **declared parsing scope**, not semantic understanding of every object in an Office or PDF file. The implementation must retain an independently verifiable inventory and per-unit outcome, and report unsupported or failed objects rather than remove them from the denominator. Owner review and format-specific tests remain required before freeze.

| Format | Enumeration and accounting | Conditions for a complete declared text/data scope | Explicit exclusions and limits |
|---|---|---|---|
| XLSX | Enumerate all sheets in workbook order, including hidden and very hidden sheets. Within each sheet enumerate cells actually represented in the source, retaining coordinates, plus merged ranges and anchors; retain declared dimensions separately. Missing coordinates are sparse/absent, not enumerated cells, while explicitly stored blanks remain distinguishable. | Every enumerated sheet, represented cell and merged range has a recorded successful outcome and source anchor, with no failures or unsupported units in the declared scope. Store formula expression and cache status independently. | A missing cached result is recorded as missing and does not imply recalculation. Full formula analysis or recalculation is not implied. Unsupported workbook objects outside the declared data scope must be reported separately. |
| DOCX | Enumerate ordered body paragraphs and table cells, nested structures where supported, and header/footer parts with part identity. Count each content-bearing unit once; retain its source part and anchor. | Every enumerated unit in the declared scope has an anchored parse outcome and no failed or unsupported units in that scope. Preserve structural ordering and identify empty units. | Drawing text, embedded objects, comments, tracked changes and fields must be inventoried or flagged unsupported when not handled; parsing body text alone cannot establish full document semantics. |
| PPTX | Enumerate every slide in presentation order and every shape in scope, retaining slide/shape identity; recursively account for groups and text-bearing descendants where supported. | Every enumerated slide and in-scope shape has an anchored outcome with no failures or unsupported in-scope units. Non-text shapes are counted and classified, not silently omitted. | Slide notes, chart data, media and embedded objects need separate supported/unsupported outcomes; parsed slide text does not mean their contents were analyzed. |
| PDF | Enumerate every page in page order. For each page record text extraction outcome and whether embedded or rendered non-text content is present or its detection is unknown. | All pages have successful outcomes for the declared text-layer scope, including explicitly empty pages; errors or unknown page count prevent a complete claim. | A complete text-layer extraction is not OCR or full visual understanding. Image-only pages and unexamined graphics must be visible as exclusions or unknowns, including when extracted text is empty. |

`enumerated_total = 0` is valid only after successful enumeration; an unknown total is represented separately and cannot satisfy `complete`. For every format, the evidence binds the immutable source checksum and parser version. Subsequent index, analysis, summary, and LLM-context coverage use their own units, evidence and completeness decisions; none inherit parser completeness automatically.

## Complete conditions

1. Source snapshot pinned to exact revision/build and canonical universe of units.
2. Ordered observed intervals form exact union covering the universe; no gaps, no out-of-scope slices, and duplicates do not inflate counts.
3. Any skipped/unsupported/corrupt units recorded explicitly; `complete=true` is not permitted if requested semantic scope excludes unhandled content without reporting partiality.
4. No proof created by LLM assertion. Evidence must derive from backend execution logs/validated index manifests.
5. Any filter-based analysis labels its restricted universe. It must never advertise `FULL_WORKBOOK` when only matched rows were processed.

`processed_intervals` use half-open, zero-based ordinal ranges `[start, end)` within the named immutable universe. Before a proof is trusted, independently verify `0 <= start < end <= enumerated_total` when total is known; sort and union intervals, reject out-of-scope units, and verify `processed_count` equals the size of the union. For `complete=true`, the union must equal `[0, enumerated_total)`, with no failed, unsupported or excluded units in that declared scope. For a successfully enumerated empty scope, `enumerated_total=0`, `processed_count=0` and an empty interval list may be complete. For unknown total, only `complete=false` is valid. The JSON Schema cannot prove interval order, overlap, equal counts, trusted provenance or authorization; application-level validation and contract tests must enforce these properties. JSON Schema `format` checks can be optional in validators: production must also parse `verified_at` as a valid timezone-aware timestamp rather than rely only on the schema's textual pattern.

## Fragmented units and coverage accounting

A source unit can be larger than one result page. `processed_intervals` and `processed_count` count **whole units whose required payload has been verified**, not units for which any fragment has merely been emitted. A first fragment of a long cell therefore contributes zero completed cell units. Completing subsequent fragments may add that cell's ordinal exactly once after the backend verifies the union of valid fragments for the same immutable source unit. A client or model cannot assert completion from a cursor, a cell coordinate or an emitted character count alone.

For a textual unit, the fragment ledger must bind document ID, revision, source checksum, applicable index build, unit identity, representation/version, total Unicode-codepoint length and each half-open codepoint interval to backend-issued execution/result evidence. Validate `0 <= start < end <= total`, union overlap without double-counting, and require `[0, total)` with no gap before marking the unit complete. A zero-length value is complete only with an explicit backend-issued observation of that empty value; absence of fragments is not proof of an observed empty cell. Fragments from different representations, units, revisions, builds, consumers or request scopes cannot be combined. If total length or fragment provenance is unknown, report partial/unknown rather than complete.

The definition of *required payload* is scoped: an XLSX formula's expression, cached-value status and any returned value are distinct fields. Emitting a cell's displayed text does not prove every required field, formula analysis, or recalculation. If a field has its own paging or is omitted/unsupported, a cell-level proof for a scope requiring that field remains incomplete. Retrieval evidence records material actually returned; LLM-context evidence must separately verify material admitted to the final provider request. A lossy transform or dropped/compacted page cannot inherit retrieval coverage as LLM coverage.

[Synthetic fragment vectors](fixtures/fragment-coverage-vectors.json) and a test-only oracle in `packages/backend/tests/test_v2_phase0_fragment_vectors.py` give concrete review examples; they are not a wire schema or verification of the eventual V2 service. This is a semantic requirement pending a frozen fragment-evidence wire schema and executable V2 verifier. Required acceptance cases: one 10,000-codepoint cell delivered in three pages; gap, duplicate and overlapping fragments; Unicode supplementary characters and escaped JSON; identical offsets from a different cell/revision/build; a source field omitted from the requested scope; and explicit observed empty versus unobserved empty. Confirm `complete` remains false until every required fragment and field is proven, even when all cell ordinals have been encountered.

## Example response (illustrative; not source-derived)

```json
{
  "coverage_type": "retrieval_coverage",
  "scope": {"document_id": "d1", "revision": 1, "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "index_build_id": "b4", "unit_type": "cell", "scope_id": "sheet-1"},
  "enumerated_total": 300,
  "processed_intervals": [[0, 120], [120, 220]],
  "processed_count": 220,
  "failed_units": [],
  "unsupported_units": [],
  "excluded_units": [],
  "complete": false,
  "proof_id": "proof-opaque",
  "verified_at": "2026-11-12T00:00:00Z"
}
```

Only claim: 220 of 300 cells returned, not that 300 were analyzed or observed by LLM.

## Default intent behavior

- `INSPECT_AND_OVERVIEW`: enumerate all sheets and sample systematically across beginning/middle/end or another declared sampling policy. For small files full scan permitted. Report `sampled`, `inspected`, `full-read` separately; do not fake full-read.
- `PREPARE_FOR_QA`: ingest/index entire supported source backend (or return pending/partial); no implied LLM visibility.
- `FULL_READ`: read specified complete source universe using structured traversal/artifacts; return incomplete if resource constraints interrupt, even if a summary exists.
- `SUMMARIZE`: run summary task with source coverage proof, but explicitly label summarized result, not raw workbook copy.
- `ANALYZE`: execute sandbox with immutable input manifest and success proof.
- `SEARCH/FILTER`: search within documented index scope, not full source semantic analysis.
- `EDIT_WITH_APPROVAL`: only previews; authoritative commit by Revision Service.

## Critical test cases

Adjacent pages joined; missing middle page; duplicated page; same offset different build; formula without cached value; hidden sheet not sampled; PDF image-only page; column/row merged; failed summary map stage; answer quoting out-of-scope anchors; exhausted tool-call budget. Verify answer gate rejects overclaims.
