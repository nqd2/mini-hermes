# V2 Persistence & Data Model — Proposed

**Status:** Proposed relational model for PostgreSQL / SQLAlchemy async. No DDL or migration executed.

See [D04a](04a-persistence-integrity-contract.md) for proposed same-resource foreign keys, environment identity, worker fencing and crash/cleanup rules. Both documents require Phase 0 owner review before implementation.

## Domain table sketch

| Domain | Table (logical) | Key and important columns |
|---|---|---|
| Auth / Open WebUI | `v2_auth_revoked_session_jtis` | proposed `jti_digest` unique, user ID, revoked/expiry times and reason; durable per-token logout candidate in [D03e](03e-auth-session-revocation-contract.md), pending Auth/DB owner review |
| Storage | `v2_documents` | `document_id`, owner/tenant, type, current_revision_id, created_at |
| Storage | `v2_upload_receipts` | receipt ID, authenticated principal, newly uploaded Open WebUI file ID, expected checksum/size, V2 flow version, created/expires/consumed state; only for source-file handoff, not V1 backfill |
| Storage | `v2_document_revisions` | `revision_id`, doc FK, revision_no, immutable_source_key, checksum_sha256, size_bytes, committed_at, publication_state |
| Storage | `v2_ingestion_jobs` | `job_id`, document FK, intended revision number, nullable revision FK until publication, idem scope/key + request_hash, source state, index state, lease/fence, retry_count, error, status timestamps |
| Storage | `v2_index_builds` | build ID, revision FK, source checksum, parser/chunker/index versions, state, manifest/checksum, completed_at |
| Storage | `v2_active_indexes` | revision FK unique, active build FK, generation |
| Storage | `v2_source_anchors` | anchor ID, build FK, kind, sheet/page/row/cell/section coordinates, offset info |
| Storage | `v2_chunks` | chunk ID, build FK, stable ordinal, source anchor boundaries, derived content checksum |
| Excel | `v2_workbook_sheets` | build FK, sheet stable ID, index/order, name, visibility, dimensions |
| Excel | `v2_workbook_cells` | build FK, sheet FK, row/col coords, type, canonical raw value, formula, cached value+state |
| Excel | `v2_merged_ranges` | build FK, sheet FK, A1 bounding range, anchor cell |
| Revision | `v2_edit_previews` | preview ID, doc/base revision FK, patch hash, created/expires_at, staged ref |
| Revision | `v2_document_approvals` | approval ID, preview FK, user/approver identity, exact patch hash, expires_at, consumed_at |
| Revision | `v2_idempotency_records` | principal/operation/key/request hash unique, stable outcome ref |
| Cross-domain | `v2_outbox` | event ID unique, event type, payload ref, delivered_at/retry |
| Runtime | `v2_conversation_engine_bindings` | conversation FK unique, engine type+version immutable |
| Runtime | `v2_run_bindings` | run FK unique, matching conversation engine version, tool/skill snapshots |
| Runtime | `v2_run_events` | `(run_id, sequence)` unique, committed payload/ref, timestamp |
| Runtime | `v2_tool_invocations` | invocation ID, run FK, request hash, safety class, state, external proof |
| Runtime | `v2_checkpoints` | run FK/version unique, engine/binding, durable state ref |
| Work | `v2_document_tasks` | task ID, doc/revision FK, type, progress, evidence, checkpoint |

Names are descriptive placeholders: verify existing naming conventions and ownership before DDL.

## Integrity invariants

1. A revision is immutable after commit; `revision_no` increases monotonically and unique per document; `current_revision_id` references a committed revision of same document.
2. `active_index_build_id` can only reference a verified/ready build of the exact revision + checksum. Index build is derived, rebuildable from immutable source.
3. A cursor and evidence record bind build ID, document ID, revision and source checksum. Retention/GC must not free pinned content prematurely; otherwise explicit cursor invalidation.
4. Formula expression, cached value and cached value status are separate (no fake recalculation). Merged cells preserve anchor and ranges.
5. Enforce DB FK and uniqueness in PostgreSQL; avoid application-only parent existence assumptions.
6. Outbox events have stable IDs with at-least-once delivery + consumer dedupe. DB commit does NOT imply object-store atomic transaction.
7. V2 namespace has environment identity; startup checks DB metadata instance ID and object namespace safety token before accepting traffic.
8. The initial upload allocates one new document/revision through the [D03d ingestion protocol](03d-ingestion-contract.md); its idempotency outcome, revision pointer and outbox event are committed together. A V1 file ID or upload-processing event is not a V2 revision or index proof.

## Index build state machine

`REQUESTED -> RUNNING -> VERIFYING -> READY -> RETIRED` or `FAILED`. Only `READY` eligible for active pointer. On worker crash, lease expiration permits idempotent resume/retry; no duplicate published active build. Active pointer change is an atomic DB transaction, with generation increment and CAS for concurrent builds.

## Source/revision publication state machine

`STAGED -> VERIFIED -> COMMITTED -> AVAILABLE` is an **operational state model**. The only public visibility boundary is DB `COMMITTED`; `AVAILABLE` is a verified post-commit service condition, not a second non-transactional publication point. API must never return an uncommitted draft as current revision. Any committed source object missing/checksum-invalid at read time raises `INTEGRITY_FAILURE` and stops access, not a false successful response.

### Commit transaction boundary

- Preconditions: staged object checksum verified, current authorization, approval identity/expiry/base revision/patch hash match, no concurrent revision conflict.
- In one DB transaction: consume approval; insert revision row; update current pointer via revision compare-and-set; write outbox; store idempotency result.
- After commit: retry with same idem scope/key and same request hash returns the same revision, including crash-after-commit-before-ACK.
- Stage-before-commit crash: unreferenced source object removed after safety interval; outbox consumers dedupe by event ID.
- Rollback: create and publish a compensating revision; never mutate history.

Current-checkout PostgreSQL publication uses a shared transaction-scoped authorization lock before its live role and resource checks; grant, group, role/status and document-topology mutations use the exclusive form. The transaction must run at `READ COMMITTED` so a writer that commits before lock acquisition is visible, and ORM identity-map state must not substitute for the live role read. [D04b](04b-authorization-publication-race.md) records the tested V1 mitigation; [D04c](04c-effective-authorization-writer-inventory.md) inventories source-level authority writers and the stale-admin move regression. V2 cannot freeze this contract until every effective-permission writer and isolation mode is accounted for.

## Transaction and concurrency proof required

Tests on disposable PostgreSQL for FK constraint enforcement, two simultaneous edits against same base revision, duplicate ingestion idempotency, stale active-index CAS, worker crash mid-index, object-store checksum failure, DB commit/ack boundary, orphan sweeper, and backup/restore. Need actual SQLAlchemy mapping design and migrations after specs freeze.
