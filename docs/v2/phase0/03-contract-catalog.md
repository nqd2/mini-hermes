# V2 Contract Catalog

**Status:** Locked / Accepted. Formal API contracts, document tool DTOs, schemas, and idempotency envelopes. Runtime semantics preserve ADR-002.

The proposed field-level document tool DTOs, cursor budget behavior and review gates are in [D03a](03a-document-tool-contracts.md). They remain subject to the same Phase 0 review and do not replace public Open WebUI contracts.

The proposed canonical patch/request hash and idempotency profile, including reference vectors and its cross-runtime limits, is in [D03b](03b-idempotency-hash-contract.md).

The proposed authenticated cursor encoding, scope digest, position, TTL and build/key lifecycle are in [D03c](03c-cursor-contract.md). This profile is not yet implemented or frozen.

The proposed upload/adoption, first-revision publication, independent source/index states and status schema are in [D03d](03d-ingestion-contract.md). Open WebUI's current file upload wire contract remains governed by [D07c](07c-file-upload-contract-inventory.md).

The proposed Auth Service session revocation contract and its no-Redis compatibility finding are in [D03e](03e-auth-session-revocation-contract.md) and [D07f](07f-auth-session-boundary-inventory.md). The durable per-token ledger is a design candidate requiring Auth/DB owner review, not an implemented V2 capability.

## Shared envelope

```json
{
  "contract_version": "2.0-draft",
  "request_id": "opaque-id",
  "document_id": "opaque-id",
  "revision_id": "opaque-id",
  "index_build_id": "opaque-id",
  "status": "complete",
  "data": {},
  "evidence": [],
  "next_cursor": null
}
```

Required semantics: stable types, explicit optionality, current authorization on every call, no credentials/ACL authority supplied by model, typed failures. Use JSON Schema/OpenAPI when owners finalize this draft; don't implement by copy-pasting this illustrative envelope.

## Proposed document tool commands

| Tool | Input concept | Output concept | Side effects |
|---|---|---|---|
| `inspect_document` | doc/revision, optional sheet selector | format, enumerated sheets/pages/sections, dimensions, status, coverage | Read-only |
| `read_document_range` | doc/revision/build, source anchor/range, max output | bounded source slices + cursor + evidence | Read-only |
| `read_spreadsheet_range` | doc/revision/build, sheet ID, A1 range, optional cursor | typed cell records + source anchors + cursor | Read-only |
| `search_document` | doc/revision/build, query/filters, pagination | ranked anchored matches, coverage | Read-only |
| `execute_document_analysis` | doc/revision, immutable analysis spec, constraints | job/ref/result + input evidence | Creates explicit durable analysis job |
| `start_document_summary` | doc/revision, summary spec/idempotency | durable job identifier | Creates explicit durable task |
| `get_document_task` | task ID | state/progress/provenance/error | Read-only |
| `export_document` | doc/revision, format, idempotency | export artifact/ref or job ID | May create explicit artifact/job |
| `propose_document_edit` | doc/base revision, patch, idempotency | validated preview/ref/patch hash | Creates draft/preview, never commits |

**Do not classify a tool as retry-safe solely by its name.** Inspect whole call path (e.g., lazy refresh, creation of jobs, audit writes). Any job-producing command requires idempotency.

## Ingestion commands (Document API boundary, not model tools)

| Command | Input concept | Output concept | Side effects |
|---|---|---|---|
| `start_ingestion` | trusted principal, bytes or authorized source-file reference, expected checksum/size, format hints, idempotency key | durable job/document/revision IDs and status reference | Stages source and creates a job; initial revision publication delegates to Revision Service |
| `get_ingestion_status` | job ID | independent source/index states, integrity and coverage evidence | Read-only with current authorization |

The LLM cannot invoke `start_ingestion` merely by naming a source file; the authenticated upload flow supplies it. Its internal and eventual versioned public contract is [D03d](03d-ingestion-contract.md).

## Proposed paging contract

`read_spreadsheet_range` returns records with `sheet_id`, `row_number`, `column_index`, `coordinate`, `type`, `raw_value`, `formula`, `cached_value_status`, `source_anchor`. Response includes `coverage` of emitted cells/ranges and `next_cursor` or null.

Cursor MUST be opaque and authenticated; internally binds principal + authorization scope, document ID, immutable revision, index build, the digest of the repeated filter/range/query/order scope, next boundary, expiration and contract version under [D03c](03c-cursor-contract.md). Every use reauthorizes current resource access. Malformed, mismatched, expired, revoked or retired-build cursors fail deterministically (never silently skip data). If large cells need sub-cell offsets, encode precisely and return a lossless continuation contract.

**Budget algorithm contract:** normalize candidate result, serialize with same settings as transport, measure complete resulting serialized JSON characters (and bytes), shrink by record boundary until `<= max_output_chars`; ensure non-empty valid cursor/metadata envelope; if minimal envelope exceeds budget return `OUTPUT_BUDGET_TOO_SMALL`. No `offset += requested_limit` assumption: only advance using emitted consumed position. For text slicing, use deterministic Unicode codepoint offsets and unambiguous encoding.

## Typed errors (proposed)

| Code | Retry? | Meaning |
|---|---|---|
| `DOCUMENT_NOT_FOUND` | No | Nonexistent or undisclosable resource (policy-dependent) |
| `FORBIDDEN` | No | Current principal is denied |
| `INVALID_ARGUMENT` | No | Invalid range/type/version |
| `UNSUPPORTED_FORMAT` | No | Format unsupported in R1 |
| `UNSUPPORTED_EDIT_OPERATION` | No | Mutation not on edit allowlist |
| `INDEX_NOT_READY` | No direct tool retry | Existing ingestion job pending; use status/subscription |
| `INDEX_FAILED` | No blind retry | Ingestion failed; explicit retry operation |
| `CURSOR_EXPIRED` | No | Restart traversal pinned to a new valid build |
| `CURSOR_INVALID` | No | Malformed, unauthenticated or revoked-key cursor |
| `CURSOR_SCOPE_MISMATCH` | No | Principal, revision, filter or build mismatch |
| `CURSOR_BUILD_RETIRED` | No | Pinned build no longer available; restart explicitly |
| `OUTPUT_BUDGET_TOO_SMALL` | No | Minimal payload cannot fit |
| `TRANSIENT_UNAVAILABLE` | Bounded only if safe | Networking/temporary read service failure |
| `INTEGRITY_FAILURE` | No | Committed data bytes mismatch or absent |
| `REVISION_CONFLICT` | No | Base revision stale |
| `APPROVAL_INVALID_OR_EXPIRED` | No | Preview, approver or patch doesn't match |
| `EXTERNAL_OUTCOME_UNKNOWN` | Reconciliation only | External side effect may have happened |

Determine actual HTTP statuses/Error DTO mapping with Open WebUI compatibility owner before freeze.

## Runtime domain contract (existing semantics; implementation signatures to verify)

- `start(request, idempotency_key)`: duplicate same key+canonical request returns same run; changed request rejected.
- `stream(run_id, after_sequence)`: durable append-before-publish; sequence monotonic; client dedups `(run_id, sequence)` and detects/reconciles gaps.
- `cancel(run_id, idempotency_key)`: durable and idempotent; unknown external outcomes not discarded.
- `resume(run_id, checkpoint_command, idempotency_key)`: only same owner/binding/checkpoint version; no repeated side effect.
- `get_status(run_id)`: owner-scoped, latest durable sequence/binding; no secrets.

**Agent approval != document approval.** Tool ledger records invocation ID, deterministic request fingerprint, safety classification, outcome, external idempotency evidence and reconciliation state.

## Events / errors / compatibility

Internal proposed event envelope includes `event_version`, `run_id`, `sequence`, `type`, `timestamp`, `payload_ref_or_delta`. Exact existing Open WebUI SSE wire format MUST be inventoried, replay-tested and preserved or versioned with all affected consumers. Do NOT deploy this logical envelope directly as public SSE without manifest approval.

## Open decisions (must freeze)

Operation-specific scope normalization and ordering, deployment key management/retention review for [D03c](03c-cursor-contract.md), distinction between missing value and explicit blank, formula cached-value fidelity, status HTTP mapping, user-visible error localization, and durable logout/revocation semantics under [D03e](03e-auth-session-revocation-contract.md). D03b and D03c now propose concrete serialization, codepoint and cursor TTL profiles, but remain subject to owner review and executable V2 tests.

The current V1 revision endpoint's already-committed-preview replay semantics differ from the proposed strict V2 idempotency fingerprint rule; see the [compatibility manifest](07-open-webui-compatibility-manifest.md). Reconcile public compatibility and contract versioning before freeze.

The V1 authorization/publication race, current-checkout PostgreSQL mitigation and remaining V2 protocol obligations are documented in [D04b](04b-authorization-publication-race.md). The V2 authorization contract and production gate remain open.
