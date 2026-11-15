# Open WebUI Compatibility Manifest — Partial Checkout Inventory

**Status: INCOMPLETE.** The following checkout-backed evidence is a starting inventory, not a complete contract or wire-format audit. Baseline commit: `3d1eeedfeed8124706bf666d4da5b0380e7ff9c1`. No live frames or golden fixtures were captured.

## Reproducible frontend discovery index

[frontend-fetch-inventory.json](frontend-fetch-inventory.json) is a generated discovery index from the current checkout, produced from `packages/frontend` with:

```bash
node scripts/phase0-api-inventory.mjs > ../../v2_draft/phase0/frontend-fetch-inventory.json
node --test scripts/phase0-api-inventory.check.mjs
```

The current snapshot contains **544 direct `fetch` candidates**, **24 calls named `api` requiring wrapper inspection**, and **one parser warning**. It records frontend-relative source file, line, enclosing caller, unevaluated URL expression and conservatively inferred HTTP method; `UNKNOWN` means the scanner could not establish the method statically. The warning on `FullHeightIframe.svelte` is caused by embedded script tags in a template string and remains listed for manual review.

This index is deliberately **not** the compatibility manifest or a complete static scan. It parses `.ts`, `.js` and Svelte script blocks, excludes test and declaration files, and flags calls named `api`; other wrappers, aliases, dynamically computed calls, generated code, Socket.IO events, transport payloads, backend producer matching, authorization and runtime traffic need separate analysis. Svelte script extraction is heuristic, so parse warnings must be reviewed and a clean parse does not prove absence of missed calls. No credentials or actual request payloads are captured. Re-run the index when source changes and review the resulting diff; do not mark P0-08 complete based on entry counts.

## Backend route discovery and conservative static pairing

The backend discovery script uses Python AST and does **not** import the application or connect to a database. From `packages/backend`:

```bash
python scripts/phase0_route_inventory.py > ../../v2_draft/phase0/backend-route-inventory.json
python scripts/phase0_route_inventory.py --pair ../../v2_draft/phase0/frontend-fetch-inventory.json ../../v2_draft/phase0/backend-route-inventory.json > ../../v2_draft/phase0/static-route-pairs.json
python -m pytest tests/test_v2_phase0_route_inventory.py -q
```

[backend-route-inventory.json](backend-route-inventory.json) records 678 static route candidates, of which 622 have a directly resolved mount path. It records decorator method/path expressions, handler, source, syntactic `Depends(...)` expressions, and whether the observed mount is conditional. One nested router is flagged for manual resolution. Dynamic registration, inherited dependencies, middleware, conditional route definitions, proxy forwarding and runtime behavior cannot be inferred reliably from this index.

[static-route-pairs.json](static-route-pairs.json) contains **231 exact static path/method candidates** and **211 parameterized path-shape candidates**. Eight static paths have no exact candidate and 94 calls remain unresolved. The matcher recognizes simple identifier or `encodeURIComponent(identifier)` path segments and known Open WebUI, OpenAI, audio, image and retrieval bases; it intentionally leaves Ollama, arbitrary expressions, `:path` converters and unknown methods unresolved. A parameterized candidate is only a shape match: route ordering, parameter values, encoding and runtime routing still require verification. Neither kind of pairing proves reachability, authorization, schema compatibility or actual UI usage.

The eight unmatched static candidates include `uploadDir`, `updateUserRole`, image configuration, retrieval query settings and reranking adapters. Source search found no direct calls to the first two; the image/retrieval adapters are imported by settings components but their runtime use and dynamic registration have not been fully established. Preserve these as audit leads, not an instruction to delete or change contracts.

A scoped, manually paired inventory for the document UI is in [D07a](07a-document-ui-contract-inventory.md). It does not replace the remaining Open WebUI-wide inventory or live contract tests.

A second scoped inventory for the dynamic terminal/tool-server proxy is in [D07b](07b-terminal-gateway-contract-inventory.md). It explains why many terminal frontend calls cannot be paired with a fixed backend route and records selected frontend and ASGI contract tests. External direct-server calls and the rest of the Open WebUI manifest remain unverified.

A third scoped inventory for Open WebUI file upload, processing status SSE and original-byte download is in [D07c](07c-file-upload-contract-inventory.md). It distinguishes upload acknowledgement, V1 processing completion and the V2 ingestion proof still to be designed and tested.

A fourth scoped inventory for chat start, Socket.IO events, task reconnect and the unconsumed AgentRuntime replay API is in [D07d](07d-chat-runtime-transport-inventory.md). It identifies the missing authenticated turn association and browser replay contract; no live reconnect proof is claimed.

A fifth scoped inventory classifies the five unresolved OpenAI adapter calls into Open WebUI proxy and direct browser-to-provider paths; see [D07e](07e-openai-proxy-and-direct-provider-inventory.md). Selected model-discovery, verification, error and chat-response behaviors have tests, but live provider, credential and SSE contracts remain open.

A sixth scoped inventory details the human-session boundary, bearer/cookie precedence and current token revocation checks; see [D07f](07f-auth-session-boundary-inventory.md). Selected ASGI cases now exercise real signed JWTs through the authentication dependency chain with persistence substituted; deployed login, Redis and browser behavior remain open.

### Unresolved pairing triage

The 94 unresolved entries in [static-route-pairs.json](static-route-pairs.json) break down by consumer source as follows. This is a workload queue, not a finding that 94 routes are missing:

| Consumer group | Entries | Next contract action |
|---|---:|---|
| `lib/apis/terminal/` including Git | 23 | D07b characterizes 22 calls from `terminal/index.ts`; add the Git request's method/path, external server variant, and live gateway checks. |
| `lib/apis/ollama/` | 16 | V2 excludes Ollama. Trace actual UI callsites and remove or version affected consumer flows without treating these as V2 provider requirements. |
| `lib/apis/openai/` | 5 | D07e classifies all five by source and selected tests; capture live direct/proxy model discovery, credential and completion stream wire behavior. |
| All other files | 50 | Manually resolve computed paths and fetch wrappers. Prioritize auth/MFA, chat tasks, file content, Mini-Hermes documents, MCP app RPC and settings; pair each with producer, auth, errors and a contract test. |

Source counts were computed from the checked-in pairing index; static pairing cannot determine runtime branch selection or whether a wrapper is invoked. The earlier 22-entry terminal statement applies only to `terminal/index.ts`, while this table includes one additional `terminal/git.ts` entry.

## Checkout-backed examples

The common frontend base is `/api/v1` (`packages/frontend/src/lib/constants.ts`). The examples below are actual consumers and producers, but request/response validation and all transitive callers remain to be inventoried. Paths in the producer column are repository-relative.

| Consumer / source | Observed contract | Producer / evidence | Remaining verification |
|---|---|---|---|
| `lib/apis/auths/index.ts`: `getSessionUser`, `userSignIn`, `userSignUp`, `userSignOut` | `GET /api/v1/auths/`; `POST /api/v1/auths/signin`, `/signup`, `/signout`; session lookup uses bearer and `credentials: include` | `main.py` mounts `routers/auths.py`; `phase0-contract.test.ts` and `test_v2_phase0_auth_contract.py` cover selected consumer and producer cases | Real authentication, complete cookie settings, MFA/OAuth, CSRF and authorization/revocation flows remain open |
| `lib/apis/chats/index.ts`: `createNewChat`, `getChatList`, `getChatById` | `POST /api/v1/chats/new` with `chat`, nullable `folder_id`, optional `variables`; `GET /api/v1/chats/` with optional paging/filter flags; `GET /api/v1/chats/{id}` | `routers/chats.py`, `models/chats.py`; consumer request and error tests in `lib/apis/chats/phase0-contract.test.ts` | Live producer responses, permissions, shared chats, import/export and downstream callers |
| `lib/apis/models/index.ts`: `getModelItems`, `createNewModel`, `updateModelById` | `/api/v1/models/list`, `/models/create`, `/models/model/update` | `main.py` mounts `routers/models.py` | Model discovery, settings, authorization and provider capabilities |
| `lib/apis/files/index.ts`: `uploadFile`, `getFileProcessStatus` | Multipart `POST /api/v1/files/`; `GET /api/v1/files/{id}/process/status?stream=true`; bearer token; process stream parses `data:` and `[DONE]` | `main.py` mounts `routers/files.py` | File ACL, streamed errors, process options, binary download and reconnect behavior |
| `routes/(app)/workspace/documents/+page.svelte`: `refresh`, `upload`, `propose`, `commit` | `/api/v1/mini-hermes/documents`, `/documents/upload`, `/documents/{id}/previews`, `/previews/{id}/commit`; commit submits `base_revision`, `base_sha256`, `artifact_sha256`, `patch_hash` | `mini_hermes/routes.py`, mounted in `main.py`; `_document_json` supplies `id`, `filename`, `revision`, Drive and sync metadata | Response/authorization matrices, malformed requests and live preview/commit tests |
| `lib/components/chat/DocumentChangeReview.svelte`: `refresh` | `GET /api/v1/mini-hermes/chats/{chatId}/previews` and `/artifacts`; separate preview/content and commit requests | `mini_hermes/routes.py` | Status variants, approval/error wire shapes and multi-user isolation |
| `lib/components/common/FileItemModal.svelte`; `DocumentViewerShell.svelte` | Source-file-to-document lookup, revision listing, document content with revision query | `mini_hermes/routes.py` | File-vs-document identifier mapping and binary response behavior |
| `lib/apis/index.ts`: task controls | `POST /api/tasks/stop/{id}`, `POST /api/tasks/chat/{chat_id}/stop`, `GET /api/tasks/chat/{chat_id}`, `POST /api/tasks/chat/{chat_id}/resume` | `routers/chat_tasks.py`; single-task stop requires admin; chat task endpoints use verified user | All status/error shapes and saved versus temporary chat authorization tests |

### Optional OpenAI passthrough boundary

`open_webui/routers/openai.py` has a deprecated, disabled-by-default generic passthrough. A regression found that valid JSON arrays and scalar values bypassed the provider gateway's hard JSON limits and could be sent upstream over 100,000 characters. The passthrough now validates the exact outbound bytes of non-object JSON and bodies declared or sent as JSON even when parsing fails, against character and UTF-8 byte ceilings; ordinary JSON objects still follow the existing gateway, and bodies not identified as JSON retain their prior route. Detection checks both inbound and effective outbound Content-Type because the latter defaults to JSON. Small malformed JSON is not schema-validated by this guard. The provider transport in the regression is mocked. This does not claim support for Ollama or prove every provider path; inventory and integration review remain open.

### Realtime transports

- Chat completion is exposed at `POST /api/chat/completions` and `POST /api/v1/chat/completions` in `packages/backend/open_webui/main.py`. `Chat.svelte` imports `createOpenAITextStream`; its actual request paths and complete stream framing still require consumer-by-consumer verification.
- Socket.IO is also part of the current UI contract: `routes/+layout.svelte` subscribes to `events`; `socket/main.py` emits an envelope containing `chat_id`, `message_id` and nested `data` with event type and payload. Source callsites also emit `chat:list`, `chat:active`, `chat:messages`, `chat:tasks:cancel`, `chat:outlet`, `embeds`, `status`, `source`, `files`, `chat:completion`, `response:completion` and `chat:message:error`; some other producers emit `citation`. This source list is not an exhaustive wire inventory and does not replace the durable AgentRuntime event schema.
- File processing has its own `data:` stream and `[DONE]` handling. Do not infer that chat, file processing and Socket.IO share framing, replay or reconnect semantics. Capture representative frames and verify each transport independently.
- Checkout characterization: `src/lib/apis/streaming/phase0-contract.test.ts` exercises split SSE frames, `[DONE]`, error, sources, selected model, usage and EOF. The current frontend parser returns `done: true` on EOF even without `[DONE]`; this is a transport observation, not durable evidence that an AgentRuntime run succeeded. End-to-end disconnect/recovery and UI behavior remain unverified.
- `packages/backend/tests/test_v2_phase0_socket_contract.py` characterizes owner and shared Socket.IO envelopes and suppression of internal notifications with a mocked emitter. It is not a live Socket.IO transport or authorization test.

### Durable runtime replay versus current UI transport

`GET /api/v1/agent/turns/{turn_id}/events` in `main.py` checks the owned turn and returns `first_available_sequence`, `latest_sequence`, `next_after_sequence`, `has_more`, `gap_detected`, `snapshot_required`, optional `snapshot`, and events with ID, sequence, type, payload and timestamps. `AgentRuntime.wrap_event_emitter` now persists every dictionary client event, including `status`, before forwarding it, and adds `runtime_turn_id`, `runtime_event_id` and `runtime_event_sequence`. It still accepts a non-dictionary value through the legacy passthrough path; the declared emitter contract is dictionary events. The sequence is a run-level durable sequence, so a consumer must not assume every sequence number corresponds to a Socket.IO message of one selected type. A recovery between DB commit and transport emit is still an integration risk and requires client deduplication by `(run_id, sequence)`.

On disposable PostgreSQL, `test_failed_emitter_leaves_unpublished_event_in_durable_replay` confirms that a failed emitter leaves a persisted, unpublished event available to replay. `test_oversized_event_has_per_event_snapshot_marker_in_replay` confirms another current distinction: if the serialized client payload exceeds the runtime threshold, the durable event contains a per-event `payload.snapshot_required` marker instead of its full event; the runtime now sets top-level `snapshot_required` when the returned page contains this marker, even when sequence numbers have no gap. The accompanying snapshot is run status, not reconstructed chat content. The live emitter receives the full event in this test. A replay client must recognize both conditions and obtain an authoritative snapshot rather than interpreting the marker as a complete payload. Snapshot contents and recovery from this case through actual UI/transport are not yet verified.

PostgreSQL regression exposed a pagination defect: for a gap with only sequence 3 in the requested page and sequences 4–5 still available, the old implementation returned `next_after_sequence=5`, even though the status snapshot did not contain those events. `AgentRepository.get_event_page` now returns the last event actually included (or the existing cursor for an empty page); the gap and snapshot-required flags remain set. If a gap has no available events, the cursor deliberately does not advance and callers must handle the gap instead of polling blindly. Two PostgreSQL regressions cover both cases. This change does not reconstruct deleted events, make the status snapshot contain chat content, or establish UI replay support.

A concurrent-append SQLite regression reproduced a status snapshot watermark older than the returned replay page. Runtime now re-reads owner-scoped status after reading a page that requires a snapshot and fails closed if ownership is unavailable. This is not an atomic snapshot of status and events; status still does not reconstruct message content. The controlled concurrent-append PostgreSQL regression now passes; actual UI recovery remains unverified.

A separate SQLite race showed a page could include an event appended after its `latest_sequence` was read. Event and first-available queries are now bounded by that watermark; later events require a subsequent replay request. This does not make the separate status, event and chat-content reads atomic or establish live-client recovery; A controlled PostgreSQL concurrent-append regression now passes; broader concurrency and UI recovery testing remain open.

`packages/backend/tests/test_v2_phase0_runtime_replay_api.py` tests the selected route's response shape and owner-denied branch through ASGI with the runtime/repository and identity mocked; existing runtime repository tests separately cover selected durable replay and gaps. A source search for `runtime_event_sequence`, `runtime_event_id`, `runtime_turn_id`, `after_sequence` and `snapshot_required` found no references under `packages/frontend/src` at this checkout. This is an audit observation, not proof about generated code or other clients. In particular, passing server replay tests does not establish frontend deduplication, gap detection or status/snapshot recovery. The V2 integration gate must test those paths through real UI and transport and preserve existing event consumers.

### Session contract: partial producer/consumer characterization

- Sign-in sends JSON `email` and `password` with cookies included. Sign-up also sends `name` and `profile_image_url`. A successful sign-in may instead return `next_step` with an MFA challenge or pending status; callers cannot assume every successful response already contains a session.
- Session lookup sends a bearer token and includes cookies. `get_session_user` obtains the token from the authorization header, cookie or request state, rejects expired decoded tokens with HTTP 401 and `detail`, and returns identity fields, `token`, `token_type`, `expires_at` and `permissions`. The refreshed token cookie is HTTP-only; SameSite and Secure derive from configuration.
- Sign-out uses `POST` with cookies included; the backend deletes authentication cookies and attempts per-token revocation when Redis is available. Without Redis, an ASGI characterization shows the same bearer JWT remains usable. The frontend clears session storage after a successful response. Detailed OAuth redirect behavior remains to be captured; see [D07f](07f-auth-session-boundary-inventory.md).
- `packages/frontend/src/lib/apis/auths/phase0-contract.test.ts` uses mocked `fetch` for request shape, response passthrough and error details. `packages/backend/tests/test_v2_phase0_auth_contract.py` exercises the session route through in-process ASGI transport: two earlier cases substitute identity/token decoding; two newer cases use signed JWTs and the real auth dependency chain with user/MFA persistence substituted. These establish selected human-session and stale-stamp behavior, not deployed login, real database/Redis authorization or browser cookie behavior. See [D07f](07f-auth-session-boundary-inventory.md).

### MFA wrapper: manually resolved frontend paths

`src/lib/apis/auths/mfa.ts` is one unresolved dynamic `fetch` candidate in the static pairing index. Its declared path union maps to eight routes in `open_webui/routers/mfa.py`, mounted at `/api/v1/auths/mfa` by `main.py`:

| Method and suffix | Route handler | Example consumer |
|---|---|---|
| `GET /status` | `get_mfa_status` | MFA settings |
| `POST /challenge` | `get_mfa_challenge` | `routes/auth/+page.svelte` |
| `POST /enroll/start` | `start_mfa_enrollment` | `MfaChallenge.svelte` |
| `POST /enroll/confirm` | `confirm_mfa_enrollment` | `MfaChallenge.svelte` |
| `POST /verify` | `verify_mfa_challenge` | `MfaChallenge.svelte` |
| `POST /replace` | `start_mfa_replacement` | MFA settings flow |
| `POST /recovery/codes` | `regenerate_mfa_recovery_codes` | `MfaManagement.svelte` |
| `POST /recover` | `redeem_mfa_reset_token` | `MfaChallenge.svelte` |

The wrapper sends same-origin credentials, `cache: no-store`, optional JSON and bearer token; backend errors become `Error(detail)` or a fallback message. `mfa-phase0-contract.test.ts` verifies these consumer request/error behaviors using mocked fetch. The mapping is source-derived and does not change generated discovery counts or prove all callers are present.

`packages/backend/tests/test_v2_phase0_mfa_api_contract.py` exercises MFA routes through ASGI with config, rate limiter and MFA service mocked. It checks incorrect origin is rejected before cookie lookup, an expired challenge clears the scoped cookie with a generic error, a reported rate limit yields 429 and `Retry-After`, and validation/database errors are sanitized; all responses checked carry `Cache-Control: no-store`. It does not establish real rate-limit enforcement, challenge lifecycle, factor validation, replay prevention, full cookie security, expiry, recovery security or server-side authorization.

### Chat contract: partial producer/consumer characterization

- `ChatForm` accepts `chat: dict`, optional nullable `variables` and optional nullable `folder_id`. The create endpoint checks folder write access when a folder is supplied. `ChatResponse` includes chat and owner IDs, title, chat payload, created/updated timestamps, archive and pin states, metadata, variables and optional folder ID.
- On lookup, `get_chat_by_id` checks accessible chat and write access, overlays active streams and context usage; a non-accessible chat raises HTTP 401 with a not-found detail. Do not change this external status silently during V2 even if the internal error taxonomy differs.
- `packages/frontend/src/lib/apis/chats/phase0-contract.test.ts` checks create request shape, paginated filter query, bearer header, lookup and error propagation with mocked `fetch`. It does not test real route permissions, database persistence or shared-chat behavior.

### Model management: partial consumer and producer characterization

- `getModelItems` sends bearer authentication and `query`, `view_option`, `tag`, `order_by`, `direction` and `page` query parameters when truthy; it forwards an abort signal. The backend `/models/list` returns `{items, total}`, checks group membership and writable model grants, and clears `params` for callers without write access. These backend authorization observations are source review, not a live permissions test.
- `createNewModel` sends `id`, `base_model_id`, `name`, `meta`, `params`, `access_grants`, `is_active` and `commit_message` when present. `updateModelById` sends the same core fields but omits `access_grants`; access grants have a separate update endpoint. Do not assume replacing the model update automatically changes sharing.
- Selected export uses repeated `ids` query parameters and the frontend returns `[]` without making a request for an empty selection. Backend export checks workspace export permission and, for selected IDs, requires every requested model to be accessible for export. Import posts `{models: [...]}` and the existing producer returns a boolean and checks import permission. These are existing V1 contracts; the roadmap's V2 versioned import/export requirement still needs an explicit compatibility decision and must not import privileged execution state.
- `packages/frontend/src/lib/apis/models/phase0-contract.test.ts` covers four selected consumer cases with mocked `fetch`, including structured import error propagation. It does not prove live backend serialization, actual permission enforcement, provider discovery, model execution, or all model management endpoints.
- `packages/backend/tests/test_v2_phase0_model_api_contract.py` exercises actual ASGI list/export routes with identity, permission decisions, group membership and persistence replaced by mocks. It verifies response serialization masks `params` for a read-only model while preserving writable model parameters, page/filter forwarding, HTTP 401 for denied workspace export permission, and HTTP 403 when a requested export ID is absent from the authorized result. It does not establish that the actual grant database returns the correct models, authenticate real users, exercise external providers or validate V2 persistence.

### Document preview and commit: partial producer characterization

- `POST /api/v1/mini-hermes/documents/{document_id}/previews` accepts `{operations: [...]}` and checks document `write` access before creating a preview; its success response contains `id` and `status`. `GET /documents/{document_id}/previews` returns the recent owner's previews with status, diff, operations, base revision and hashes.
- `POST /api/v1/mini-hermes/previews/{preview_id}/commit` accepts `base_revision`, `base_sha256`, `artifact_sha256` and `patch_hash`; all three hashes must be 64 lowercase hexadecimal characters. The optional `Idempotency-Key` header is forwarded with authenticated identity and approval to Revision Service. The response includes `revision`, nullable `sync_job_id` and `sync_status`; malformed request bodies receive HTTP 422 before service invocation.
- `packages/backend/tests/test_v2_phase0_document_api_contract.py` exercises those HTTP DTOs, delegation and route behavior when a mocked permission check denies access. The tests replace the identity, database, storage and service dependencies; they do not prove Revision Service's authorization, transaction, object integrity or crash-recovery guarantees. Those need separate service/integration evidence.
- Current `commit_preview` checks for a revision already published from that preview before checking whether an idempotency key belongs to another preview. For an already committed preview it rechecks current write access and returns the existing revision; it does not compare the new approval body or key on that branch. The proposed V2 contract requires a fingerprint mismatch under the same key to conflict. Treat this as a contract distinction to resolve in Phase 0 rather than silently assuming V1 already implements the stricter behavior.

### Important gaps

This is not the full P0-08 inventory. The chat/run-control producer mapping, complete schemas, auth/RBAC/session rules, existing SSE/Socket.IO event variants, errors, golden fixtures, upstream comparison, external integrations and runtime reconnection behavior remain open. The workspace document UI consumes the existing `set_cell` and `replace_text` operations; any narrower V2 edit allowlist requires an explicitly approved compatibility decision and updated consumers, not silent rejection.

## Boundary inventory to be populated from checkout

| ID | Consumer / feature | Contract category to inventory | Required test | Source/status |
|---|---|---|---|---|
| UI-01 | Login/logout | endpoints, request/response, cookies, CSRF, expiry | session integration | locate actual frontend callsite |
| UI-02 | RBAC/user identity | user/principal payload, 401/403 | revoked permission | locate actual callsite |
| UI-03 | Chat creation/list/history | API and persisted visible chat shape | create/reopen | locate actual callsite |
| UI-04 | Model settings/discovery | request/response/model list | choose model/change settings | locate actual callsite |
| UI-05 | Chat streaming | SSE/event names, wire payload, heartbeat, terminal framing | live stream + reconnect | locate actual callsite |
| UI-06 | Run controls | start/cancel/resume/status | duplicate/cancel/replay | locate actual callsite |
| UI-07 | File upload/attachment | file IDs, multipart body, authorization | upload and attach | locate actual callsite |
| UI-08 | Workspace documents | listing/metadata/preview/confirm/rollback/download | file E2E | locate actual callsite |
| UI-09 | Document ingestion status | pending/failed/ready | running-worker test | new V2 consumer proposal |
| UI-10 | Coverage display | scope and evidence renderer | partial/complete truthful UI | new V2 consumer proposal |
| UI-11 | Error presentation | errors, status codes, localization | 400/401/403/409/5xx | locate actual callsite |
| UI-12 | OAuth/Drive integration | redirect, nonce, sync status, permissions | conditional existing-feature E2E | locate actual callsite |
| UI-13 | Import/export | versioned payload compatibility | import/export/regression | locate actual callsite |

## Required actual inventory columns

For every row, add: exact frontend file/symbol, HTTP method+path OR event name, request schema, response schema, required headers/cookies, status/error shape, authorization rule, backend handler, test ID, producer owner, consumer owner, change policy (unchanged/new-versioned), verification evidence, SHA.

## Audit protocol in repository checkout

1. Record exact commit SHA: `git rev-parse HEAD` and ensure expected branch.
2. Search frontend API adapters and `fetch`/`EventSource`/websocket usages; collect typed consumers and transitive callsites (not only string grep). Search backend router registrations and SSE response generators; pair each producer to its consumer.
3. Compare with `references/open-webui` without editing preserved upstream reference; identify Mini-Hermes-specific deviations.
4. Capture representative redacted request/response and event frames from a disposable local test environment (never production credentials).
5. Pin golden contract fixtures; write consumer-driven contract tests and E2E smoke tests, including reconnect and error paths.
6. Propose any incompatibility as separate versioned change affecting all consumers; do not quietly patch frontend to mask regressions.

## Example audit commands (for developer to run; not executed here)

```bash
git rev-parse --verify HEAD
rg -n 'fetch\(|EventSource|WebSocket|/api/|/chat/completions|/documents' packages/frontend/src
rg -n 'APIRouter|router\.|StreamingResponse|text/event-stream|mini-hermes' packages/backend/open_webui
rg -n 'api/v1|fetch\(' references/open-webui
```

Searches must be followed by semantic inspection; matches are not themselves a complete API catalog. Live compatibility cannot be marked green from this draft.

### Preview metadata after permission revocation

`GET /previews/{id}` now checks current document read access before returning metadata or backfilling hashes. `GET /chats/{chat_id}/previews` filters previews whose document is no longer readable; a chat attachment alone does not grant access. Both continue to require the existing preview owner / chat-owner checks. Regression tests use real SQLite grants and routes with group lookup and chat ownership stubbed; live authentication, inherited groups, concurrent revocation and PostgreSQL are not covered. The chat query first selects currently accessible attached documents before applying its 10-preview limit, then rechecks access on each returned item. Concurrent permission changes can still leave fewer than 10 items; atomic cross-transaction authorization is not established. The wider authorization/publication race remains unresolved.
