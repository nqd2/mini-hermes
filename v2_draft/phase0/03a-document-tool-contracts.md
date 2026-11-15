# V2 Document Tool Contracts — Proposed R1

**Status:** Review draft, not frozen and not evidence of an implemented API. This document refines [D03](03-contract-catalog.md) for Phase 0. All IDs are opaque strings, not filesystem paths or authorization claims. The authenticated principal is supplied by Auth Service, never by a model argument.

## Common types and invariants

| Type | Required fields | Meaning |
|---|---|---|
| `RevisionRef` | `document_id: string`, `revision: integer`, `source_sha256: string` | Immutable source identity. Revision is positive and checksum is lowercase SHA-256 hex. |
| `IndexRef` | `build_id: string`, `parser_version: string`, `chunker_version: string`, `schema_version: string` | Derived index identity; it belongs to exactly one `RevisionRef`. |
| `Anchor` | `kind: string`, `document_id: string`, `revision: integer`, `source_sha256: string`, `location: object` | Source location, not an access capability. Spreadsheet locations include sheet ID and cell/range; other formats use part/page/slide anchors. |
| `Evidence` | `evidence_id: string`, `revision: RevisionRef`, `index: IndexRef or null`, `anchors: array[Anchor]` | Backend-issued provenance tied to the actual result. |
| `Coverage` | `coverage_type`, `scope`, `enumerated_total`, `processed_intervals`, `processed_count`, `failed_units`, `unsupported_units`, `excluded_units`, `complete`, `proof_id`, `verified_at` | Proposed structural schema: [coverage-proof.schema.json](schemas/coverage-proof.schema.json); semantics and independent proof obligations: [D05](05-coverage-semantics.md). Unknown total cannot be complete. |
| `Problem` | `code: string`, `message: string`, `retryable: boolean`, `request_id: string` | Public-safe error. Include neither credentials nor document text. Diagnostic logs may carry correlation IDs. |

`inspect`, `read`, `search` and task-status calls are read-only with respect to index/job creation. Each call authenticates and rechecks current authorization before exposing existence, data, artifacts, cursor state or evidence. Access denial follows the existing public endpoint's disclosure policy; the V2 internal code alone does not select a public HTTP status.

## Request and result types

The following fields specify the minimum internal tool DTOs, not public Open WebUI wire replacements. The actor, execution budget and deadline are injected by the trusted adapter, rather than accepted from LLM arguments. Optional fields are marked `?`; omitted and explicit `null` are not interchangeable unless specified below.

| Operation | Input fields | Success result | Side effects / failure |
|---|---|---|---|
| `inspect_document` | `document_id`, `revision?` | `revision: RevisionRef`, `format`, `units`, `parser_coverage`, `index_status` | No job creation. Report failed, unsupported or unavailable enumeration explicitly. |
| `read_document_range` | `document_id`, `revision`, `build_id`, `anchor`, `cursor?` | `revision`, `index: IndexRef`, `items`, `evidence`, `retrieval_coverage`, `next_cursor: string or null` | `INDEX_NOT_READY` if a usable build is unavailable; do not create one. |
| `read_spreadsheet_range` | `document_id`, `revision`, `build_id`, `sheet_id`, `range`, `cursor?` | Read result above with ordered cell records including coordinate, value type, raw value and formula/cache status | Same as range read. Out-of-scope or mismatched cursor is rejected. |
| `search_document` | `document_id`, `revision`, `build_id`, `query`, `cursor?` | Read result above with ordered anchored matches and explicitly scoped search coverage | No index mutation; zero matches alone is not evidence of full semantic analysis. |
| `execute_document_analysis` | `document_id`, `revision`, `analysis_spec`, `idempotency_key` | `task_id`, `status`, `revision`, `input_scope` | Creates a durable sandbox job. Retries with the same key and request return the same operation. |
| `start_document_summary` | `document_id`, `revision`, `summary_spec`, `idempotency_key` | `task_id`, `status`, `revision`, `input_scope` | Creates a durable task; task completion is not verbatim LLM-context coverage. |
| `get_document_task` | `task_id` | `task_id`, `status`, `revision`, `progress`, `evidence?`, `problem?` | Owner/resource authorization is checked on each query. |
| `export_document` | `document_id`, `revision`, `format`, `idempotency_key` | `status`, `artifact_ref?`, `task_id?`, `revision` | May create an export artifact or job; an artifact reference remains access controlled. |
| `propose_document_edit` | `document_id`, `base_revision`, `operations`, `idempotency_key` | `preview_id`, `base_revision`, `patch_hash`, `expires_at`, `status` | Creates only a preview. Revision Service validates the allowlist, approval and commit separately. |

No write operation may infer its authority from a caller-provided `user_id`, a previous successful cursor or LLM-provided approval state. For each idempotent command, uniqueness is scoped to the authenticated principal, operation and key; canonical request mismatch returns conflict. Cached results are returned only after current access is checked.

## Cursor and output budget

Cursor is an opaque, authenticated, versioned continuation token. Its [proposed D03c profile](03c-cursor-contract.md) binds principal, document, revision, checksum, build, a digest of query/range/order scope, next consumed position and expiry. The caller repeats the same validated semantic scope on continuation. Authenticate and apply disclosure policy before exposing cursor state; verify the MAC before using payload fields, then recheck current permission and build availability. A stale, invalid, expired or cross-scope token must not restart at offset zero or silently skip content. Retiring a build invalidates its cursors explicitly.

The trusted adapter supplies the maximum serialized-result character budget. Measure the exact complete tool result using the same serialization settings that the model adapter will use. Shrink along record boundaries; advance only over emitted data. Oversized individual cells expose an anchored, codepoint-offset continuation with no loss or duplication. Encountering or emitting a partial cell does not establish cell-level retrieval coverage: completion requires verified fragment and field coverage as specified in [D05](05-coverage-semantics.md#fragmented-units-and-coverage-accounting). Separately record UTF-8 byte and token usage; neither substitutes for character measurement.

If even the minimum successful envelope or progress cursor cannot fit, return `OUTPUT_BUDGET_TOO_SMALL` without advancing. The error response itself must fit the available transport budget; if it cannot, fail the enclosing request with a bounded transport-level error rather than emitting an oversized tool result or an empty page with an unchanged cursor.

## Result identity across orchestration boundaries

The result used for model context, recorded evidence, citations, UI display and durable replay must all refer to the same emitted page and immutable revision/build. The existing V1 execution wrapper updates reserved tool parameters through `get_updated_tool_function`; `tool_execution._invoke_tool` currently supplies messages, files and scope but no resource output budget. The built-in tool spec filters reserved `__...` parameters, but any proposed injected budget must still be reviewed across direct, resumed and durable invocation paths and must not become a model-controlled parameter. The durable tool ledger may bound or externalize output before the resource adapter, so tests must verify the actual result shape through that boundary rather than assume a raw dictionary. In the existing V1 path, `prepare_resource_tool_result` returns serialized content and a supplied flag, while citation construction in middleware and tool resumption may still receive the original tool result. Consequently, shortening only the serialized string in that adapter could cause citations to describe text that was not supplied to the model. Any implementation that repaginates after a read must pass the adjusted typed result consistently through all consumers, recompute the range-bound evidence ID, and verify no raw larger result is exposed or recorded as the supplied page. The V2 adapter must also account for actual downstream serialization/escaping; this observation does not authorize changing V1 behavior or claim the current citation path has already emitted such a mismatch.

## Coverage claims and incomplete work

All coverage claims name an exact requested scope and domain. Parse, index, retrieval, analysis, summary and LLM-context proofs are independent. An indexed workbook does not establish that the model received every cell; summary evidence does not prove verbatim reading. Failed, unsupported, unknown or excluded source units are exposed in the proof. Gate a complete answer on the evidence required by the actual user request, not on a model declaration.

## Review gates

Before this draft is frozen, review and test the D03c cursor encoding/key rotation, retention and TTL, format-specific anchors, D03b canonical request/patch hashing, status mapping and compatibility behavior against existing UI consumers. Validate JSON schema and failure examples with contract tests; document explicit exceptions for deployments where a provider or object store cannot implement the required semantics. The document is not permission for production migration, reset or traffic changes.
