# V2 Persistence & Data Model — Greenfield Specification

**Status:** Locked / Accepted. Greenfield relational model for PostgreSQL / SQLAlchemy async.

See [D04a](04a-persistence-integrity-contract.md) for composite resource foreign keys, environment identity, worker fencing and crash/cleanup rules; [D04b](04b-authorization-publication-race.md) for the authorization publication race mitigation; and [D04c](04c-effective-authorization-writer-inventory.md) for the effective authorization writer inventory.

---

## 1. Master Table Catalog (49 Tables under Official `mh_*` Prefix)

Mini-Hermes V2 establishes a clean, greenfield relational persistence schema using PostgreSQL and SQLAlchemy async under the official `mh_*` table prefix. All tables enforce composite foreign keys, explicit column types, and strict publication/execution state machines.

### 1.1 Storage & Core Documents (10 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 1 | `mh_documents` | `id` (UUID) | `current_revision_id` -> `mh_document_revisions(id)` (nullable until 1st commit) | `(tenant_id, name)` index, `owner_id` index | Root entity for documents; tracks ownership, tenant, format type, and current committed revision pointer. |
| 2 | `mh_document_revisions` | `id` (UUID) | `document_id` -> `mh_documents(id)` ON DELETE RESTRICT | `UNIQUE(document_id, revision_no)`, `checksum_sha256` index | Immutable committed revision record. Stores immutable object storage key, SHA-256 digest, byte count, and publication timestamp. |
| 3 | `mh_upload_receipts` | `id` (UUID) | none | `openwebui_file_id` index, `(principal_id, expires_at)` index | Temporary bridge record tracking an uploaded source file before initial revision adoption and verification. |
| 4 | `mh_ingestion_jobs` | `id` (UUID) | `document_id` -> `mh_documents(id)`, `revision_id` -> `mh_document_revisions(id)` (nullable) | `UNIQUE(idempotency_scope, idempotency_key)`, `lease_worker_id` index | Durable ingestion state machine tracking format validation, parsing, and index build orchestration. |
| 5 | `mh_index_builds` | `id` (UUID) | `document_id` -> `mh_documents(id)`, `revision_id` -> `mh_document_revisions(id)` | `(revision_id, build_state)` index, `manifest_hash` index | Derived index artifact tied to an exact immutable source revision, parser version, chunker version, and schema version. |
| 6 | `mh_active_indexes` | `revision_id` (UUID) | `revision_id` -> `mh_document_revisions(id)`, `active_build_id` -> `mh_index_builds(id)` | `UNIQUE(revision_id, active_build_id)` | Pointer to the authoritative active index build for a document revision. Updated only via compare-and-set transaction. |
| 7 | `mh_source_anchors` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)` ON DELETE CASCADE | `(index_build_id, anchor_kind, sheet_id, page_no)` index | Fine-grained structural coordinates (cell, row, paragraph, slide, page) linking parsed content to exact source locations. |
| 8 | `mh_chunks` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)`, `anchor_start_id` -> `mh_source_anchors`, `anchor_end_id` -> `mh_source_anchors` | `UNIQUE(index_build_id, stable_ordinal)` | Searchable/retrievable text chunks bounded by source anchors with token counts and content SHA-256. |
| 9 | `mh_object_registry` | `checksum_sha256` (CHAR(64)) | none | `object_key` UNIQUE, `ref_count` index | Content-addressed immutable storage registry tracking byte counts, storage tier, and active document reference counts. |
| 10 | `mh_staged_objects` | `id` (UUID) | none | `checksum_sha256` index, `expires_at` index | Staged object tracker for uploads and proposed edit outputs prior to commit or garbage collection. |

### 1.2 Format Canonical Tables (7 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 11 | `mh_workbook_sheets` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)` ON DELETE CASCADE | `UNIQUE(index_build_id, sheet_order)`, `UNIQUE(index_build_id, sheet_name)` | Preserves workbook sheet order, visibility (`visible`, `hidden`, `very_hidden`), and declared sheet dimensions. |
| 12 | `mh_workbook_cells` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)`, `sheet_id` -> `mh_workbook_sheets(id)` ON DELETE CASCADE | `UNIQUE(sheet_id, row_idx, col_idx)` | Canonical spreadsheet cell preserving coordinate (e.g., A1), raw data type, raw value, formula `<f>`, and cached value state. |
| 13 | `mh_merged_ranges` | `id` (UUID) | `sheet_id` -> `mh_workbook_sheets(id)` ON DELETE CASCADE, `anchor_cell_id` -> `mh_workbook_cells(id)` | `(sheet_id, range_coordinate)` index | Preserves merged cell boundaries (e.g. `B2:D5`) and binds non-anchor cells to the master anchor coordinate. |
| 14 | `mh_document_sections` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)` ON DELETE CASCADE | `UNIQUE(index_build_id, section_order)` | Preserves document sections, headers, and footers for DOCX structured representations. |
| 15 | `mh_document_paragraphs` | `id` (UUID) | `section_id` -> `mh_document_sections(id)` ON DELETE CASCADE | `UNIQUE(section_id, paragraph_order)` | Stores structured paragraph runs, style properties, and hyperlink targets with XML run boundary preservation. |
| 16 | `mh_presentation_slides` | `id` (UUID) | `index_build_id` -> `mh_index_builds(id)` ON DELETE CASCADE | `UNIQUE(index_build_id, slide_order)` | Preserves presentation slide ordering, slide layouts, titles, and speaker notes. |
| 17 | `mh_presentation_shapes` | `id` (UUID) | `slide_id` -> `mh_presentation_slides(id)` ON DELETE CASCADE, `parent_shape_id` (self-ref nullable) | `(slide_id, shape_type)` index | Stores shapes, text boxes, and group shape hierarchies with geometry and text frames. |

### 1.3 Revision & Approvals (5 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 18 | `mh_edit_previews` | `id` (UUID) | `document_id` -> `mh_documents(id)`, `base_revision_id` -> `mh_document_revisions(id)` | `canonical_patch_hash` index, `expires_at` index | Holds generated diff preview, canonical patch hash, and staged replacement object ref with 15-minute TTL. |
| 19 | `mh_document_approvals` | `id` (UUID) | `preview_id` -> `mh_edit_previews(id)`, `document_id` -> `mh_documents(id)` | `UNIQUE(preview_id)`, `(approver_user_id, expires_at)` index | Records explicit human approval of an exact patch hash and base revision. Consumed atomically upon commit. |
| 20 | `mh_idempotency_records` | `id` (UUID) | none | `UNIQUE(principal_id, operation_kind, idempotency_key)` | Enforces idempotency for mutation endpoints; stores request hash and cached serialized response. |
| 21 | `mh_outbox` | `id` (UUID) | none | `(event_type, published_at)` index, `created_at` index | Transactional outbox table ensuring reliable event publishing across boundaries without distributed transactions. |
| 22 | `mh_compensating_revisions` | `id` (UUID) | `document_id` -> `mh_documents(id)` | `(document_id, compensating_revision_no)` index | Records compensating rollback revisions that revert faulty commits while strictly preserving immutable history. |

### 1.4 AgentRuntime Engine (8 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 23 | `mh_conversation_engine_bindings` | `conversation_id` (UUID) | none | `(engine_type, engine_version)` index | Pins each conversation to an immutable runtime engine type and version (`mini-hermes.agent_runtime`, `v2`). |
| 24 | `mh_run_bindings` | `run_id` (UUID) | `conversation_id` -> `mh_conversation_engine_bindings(conversation_id)` | `UNIQUE(run_id, conversation_id)` | Binds each execution run to the matching conversation engine version and frozen tool/skill snapshots. |
| 25 | `mh_runs` | `id` (UUID) | `conversation_id` -> `mh_conversation_engine_bindings(conversation_id)` | `(owner_id, status)` index, `created_at` index | Tracks top-level execution runs across states (`pending`, `running`, `waiting_approval`, `completed`, `cancelled`). |
| 26 | `mh_run_events` | `id` (UUID) | `run_id` -> `mh_runs(id)` ON DELETE CASCADE | `UNIQUE(run_id, sequence)` | Monotonically sequenced durable event log per run. Persisted before emission; used for gap detection and stream replay. |
| 27 | `mh_tool_invocations` | `id` (UUID) | `run_id` -> `mh_runs(id)` ON DELETE CASCADE | `UNIQUE(run_id, idempotency_key)`, `status` index | Durable tool invocation ledger tracking parameters hash, execution state, output artifacts, and quarantine status. |
| 28 | `mh_checkpoints` | `id` (UUID) | `run_id` -> `mh_runs(id)` ON DELETE CASCADE | `UNIQUE(run_id, version)` | Durable execution checkpoints containing memory frames, turn state, and execution graphs. |
| 29 | `mh_agent_artifacts` | `id` (UUID) | `run_id` -> `mh_runs(id)` | `checksum_sha256` index, `created_at` index | Stores large tool outputs (> bounded threshold) as checksummed artifacts, returning bounded references to context. |
| 30 | `mh_execution_leases` | `run_id` (UUID) | `run_id` -> `mh_runs(id)` ON DELETE CASCADE | `(worker_id, expires_at)` index | Distributed worker lease and heartbeat fencing mechanism preventing split-brain execution across runners. |

### 1.5 Task Workers (5 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 31 | `mh_document_tasks` | `id` (UUID) | `document_id` -> `mh_documents(id)`, `revision_id` -> `mh_document_revisions(id)` | `(task_type, status)` index, `created_at` index | Asynchronous document processing task tracking (ingest, parse, chunk, index, summary, analysis). |
| 32 | `mh_analysis_jobs` | `id` (UUID) | `task_id` -> `mh_document_tasks(id)` ON DELETE CASCADE | `(document_id, status)` index | Long-running analysis execution job record tracking specification JSON, input proofs, and verified outcomes. |
| 33 | `mh_summary_tasks` | `id` (UUID) | `task_id` -> `mh_document_tasks(id)` ON DELETE CASCADE | `(document_id, status)` index | Summary generation task tracking input scope, structured summary outputs, and exact citation anchors. |
| 34 | `mh_export_jobs` | `id` (UUID) | `document_id` -> `mh_documents(id)`, `revision_id` -> `mh_document_revisions(id)` | `(document_id, target_format)` index | Document export job generating standalone artifacts in supported target formats without mutating originals. |
| 35 | `mh_worker_leases` | `task_id` (UUID) | `task_id` -> `mh_document_tasks(id)` ON DELETE CASCADE | `(worker_node_id, lease_expires_at)` index | Fencing token and heartbeat lease for asynchronous background worker nodes. |

### 1.6 Auth / RBAC / Open WebUI Async Entities (14 Tables)

| # | Table Name | Primary Key | Key Foreign Keys | Unique Constraints & Indexes | Purpose & Description |
|---|---|---|---|---|---|
| 36 | `mh_users` | `id` (UUID) | none | `UNIQUE(email)`, `role` index, `is_active` index | Greenfield user principal entity supporting Open WebUI authentication and async session binding. |
| 37 | `mh_auth_sessions` | `id` (UUID) | `user_id` -> `mh_users(id)` ON DELETE CASCADE | `session_token_hash` UNIQUE, `expires_at` index | Active user sessions tracking bearer tokens, client IP, user agent, and expiration timestamps. |
| 38 | `mh_auth_revoked_session_jtis` | `jti_digest` (CHAR(64)) | `user_id` -> `mh_users(id)` ON DELETE CASCADE | `expires_at` index, `user_id` index | Durable per-token JTI revocation ledger ensuring fail-closed session invalidation even when Redis is offline. |
| 39 | `mh_roles` | `id` (UUID) | none | `UNIQUE(name)` | Role definitions (`admin`, `user`, `reviewer`, `auditor`) with associated system permissions. |
| 40 | `mh_groups` | `id` (UUID) | `created_by` -> `mh_users(id)` | `name` index | User group entity supporting team-based access inheritance and shared workspaces. |
| 41 | `mh_group_memberships` | `(group_id, user_id)` | `group_id` -> `mh_groups(id)`, `user_id` -> `mh_users(id)` | `user_id` index, `group_id` index | Associative table binding users to groups with group-specific roles (`member`, `manager`). |
| 42 | `mh_access_grants` | `id` (UUID) | `granted_by` -> `mh_users(id)` | `(resource_type, resource_id, grantee_type, grantee_id)` UNIQUE | Fine-grained discretionary access grants (`read`, `edit`, `admin`) on documents, folders, and workspaces. |
| 43 | `mh_folder_nodes` | `id` (UUID) | `parent_folder_id` -> `mh_folder_nodes(id)`, `owner_id` -> `mh_users(id)` | `(parent_folder_id, name)` index | Hierarchical document folder topology supporting recursive permission inheritance. |
| 44 | `mh_chats` | `id` (UUID) | `user_id` -> `mh_users(id)`, `folder_id` -> `mh_chat_folders(id)` | `user_id` index, `created_at` index | Chat session entity bound to authenticated user and conversation engine binding. |
| 45 | `mh_chat_messages` | `id` (UUID) | `chat_id` -> `mh_chats(id)` ON DELETE CASCADE | `(chat_id, created_at)` index, `turn_id` index | Chat messages with structured tool call payloads, citations, and content annotations. |
| 46 | `mh_chat_folders` | `id` (UUID) | `user_id` -> `mh_users(id)` | `(user_id, name)` index | Organizational folders for user chat sessions. |
| 47 | `mh_models` | `id` (VARCHAR(128)) | none | `is_active` index | Model registry tracking authorized upstream providers and direct completion configurations (excluding Ollama). |
| 48 | `mh_model_params` | `id` (UUID) | `model_id` -> `mh_models(id)` ON DELETE CASCADE | `model_id` UNIQUE | Model-specific parameters including system prompts, sampling hyperparameters, and context limits. |
| 49 | `mh_environment_markers` | `key` (VARCHAR(64)) | none | `UNIQUE(instance_id)` | Startup safety marker recording instance UUID, environment identity, schema version, and safety token. |

---

## 2. Integrity Invariants

1. **Monotonic Revisions**: `mh_document_revisions.revision_no` increases strictly monotonically per document. A revision is completely immutable once committed.
2. **Current Revision Pointer**: `mh_documents.current_revision_id` references a committed revision belonging to that exact document; updated only via atomic CAS.
3. **Active Index Integrity**: `mh_active_indexes.active_build_id` must point to an index build in `READY` status matching the exact revision and source SHA-256 digest.
4. **Formula Separation**: In `mh_workbook_cells`, formula expression (`formula_expression`), raw value (`raw_value`), and cached value (`cached_value`) are stored in distinct columns with an explicit `cached_value_state` (`PRESENT`, `MISSING`, `DIRTY`).
5. **Durable Event Stream**: `mh_run_events` sequences are strictly increasing without gaps. Events are committed to PostgreSQL before emission over network transports.
6. **Side-Effect Quarantine**: Non-idempotent external tool outcomes that fail ambiguously are flagged `status = 'unknown'`, `quarantined = true` in `mh_tool_invocations`, blocking turn resumption until operator/reconciliation clearance.
7. **Fail-Closed Startup**: `mh_environment_markers` is verified at startup. If the database lacks the expected instance identity or matches V1 metadata, the system fails closed immediately.

---

## 3. Index Build State Machine

```text
REQUESTED -> RUNNING -> VERIFYING -> READY -> RETIRED
                                  -> FAILED
```
- Only `READY` builds can be set as `mh_active_indexes`.
- Worker crash or lease expiration triggers lease reassignment; duplicate builds are rejected by unique constraints.

---

## 4. Source / Revision Publication State Machine

```text
STAGED -> VERIFIED -> COMMITTED -> AVAILABLE
```
- **STAGED**: Raw replacement bytes stored in object storage with checksum recorded in `mh_staged_objects`.
- **VERIFIED**: Checksum and structural validity confirmed against format requirements.
- **COMMITTED**: The single transactional publication boundary in PostgreSQL. In one atomic ACID transaction:
  1. Consume `mh_document_approvals` (verifying 15-minute expiration and exact patch hash).
  2. Insert new row into `mh_document_revisions`.
  3. Advance `mh_documents.current_revision_id` using compare-and-set on `base_revision_id`.
  4. Record entry in `mh_outbox`.
  5. Store result in `mh_idempotency_records`.
- **AVAILABLE**: Post-commit verified state where committed object is accessible in storage.
- **Rollback**: Performed exclusively via `mh_compensating_revisions` generating a new forward revision; past revisions are never overwritten or deleted.
