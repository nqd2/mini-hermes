# Mini-Hermes Agent Instructions

These instructions apply to the entire repository. Follow the latest user request when it gives a more specific instruction. Keep this file aligned with the accepted architecture records and implementation plan:

- [ADR-001: Production AgentRuntime path](docs/adr/ADR-001-agent-runtime-production-path.md)
- [ADR-002: Runtime contract and service boundaries](docs/adr/ADR-002-agent-runtime-contract-domain-boundaries.md)
- [Production AgentRuntime implementation plan](docs/plans/production-agent-implementation-plan.md)

## Repository and workflow

- `packages/backend` contains the Open WebUI backend, Mini-Hermes APIs, runtime, document workers, and migrations.
- `packages/frontend` contains the Svelte application.
- `references/open-webui` is preserved upstream source for comparison. Do not edit it unless the task explicitly targets upstream reference material.
- Work in the supplied checkout. Do not create a worktree.
- If a new branch is needed, use only `feature/<feature-name>`, `dev`, or `main`. Never create a `codex/*` branch.
- Preserve production data, product API contracts, permissions, and Open WebUI license and copyright notices. The user approved a fresh persistence baseline and reset of the verified local development DB during the separate persistence implementation; this does not authorize production deletion. Do not commit `.env` files, credentials, or tokens.
- Use the existing stack and dependencies. Add a dependency only when the task requires it and the user approves that design.

## Product and architecture decisions

- Mini-Hermes is a production product for **10–20 users in a rolling 30-day window**. Do not lower reliability, security, recovery, or data-integrity requirements to MVP shortcuts.
- The current AgentRuntime is the only production execution path. DeerFlow is a conditional evaluation candidate, not a default migration.
- The approved target replaces persistence for both Mini-Hermes and Open WebUI using PostgreSQL and SQLAlchemy async. Do not retain models, backfills, history imports, or adapters solely for legacy data. Preserve the UI/Auth/RBAC/chat/model-management capabilities. User-facing import/export accepts the new versioned format.
- Ollama is outside the product target: no Ollama provider selection, discovery, health/about probes, model management, or embedding configuration. Do not install or start Ollama to suppress connection errors.
- Use the [Vietnamese use-case, design, test, and audit specification](docs/design/mini-hermes-use-cases-design-and-tests.md) for the detailed target; its test scenarios are planned evidence, not completed production gates.
- Open WebUI owns product UI, authentication, RBAC, chat, and model management. Keep its current license notices and track the rolling 30-day user count. Do not pursue a new license or downgrade Open WebUI without a real licensing need.
- The **99.9% availability target is internal**, not a committed SLA.
- Each conversation stays bound to one engine and version. Each run has one execution owner and a matching run binding. Never resume a checkpoint across runtimes. Canary and rollback change engine selection only for new conversations.

## Service ownership

- **Auth Service / Open WebUI** authenticates the user and supplies the authenticated principal.
- **AgentRuntime** owns conversation and run orchestration, checkpoints, durable events, cancellation, recovery, and agent-approval pause/resume. It must not write directly to the document database or document object storage.
- **Document API** owns `read`, `inspect`, `search`, `propose_edit`, and `export`. A document commit endpoint must delegate to Revision Service.
- **Revision Service** is the only authority for patch validation, diff preview, current resource authorization, document approval, revision preconditions, atomic commit, and rollback.
- **MCP Gateway** owns tool discovery, connections, credentials, invocation, isolation, timeouts, rate limits, and audit.
- **Skill Runtime** owns skill registry, immutable versioning, loading, permissions, and sandboxed execution.
- **Document workers and storage** own durable document jobs and immutable staged objects/artifacts. Staged objects must not appear as committed revisions.
- Keep agent approval separate from document approval. AgentRuntime may pause and resume a run; only Revision Service may approve and commit a document patch.

## Runtime, events, and side effects

- Implement the runtime contract through `start`, `stream(after_sequence)`, `cancel`, `resume`, and `get_status`.
- Persist each event before publishing it. Event sequence increases monotonically within a run. Replay from the durable log, deduplicate by `(run_id, sequence)`, and use status/snapshot plus replay to recover stream gaps. Never silently skip a gap.
- Make start, cancel, resume, and tool invocation retries idempotent. Use the durable invocation ledger and provider idempotency keys where available.
- If an external side effect may have happened but its result is unknown, quarantine it for reconciliation. Do not retry it blindly.
- Bound tool output. Store large results as durable artifacts and return bounded references or excerpts to model context.
- Keep filesystem access local through tools. Do not serialize or upload the entire repository to a provider; retrieve only the files and ranges needed for the task.

## Context and provider request limits

- Internal request preparation must stay within **95,000 characters**.
- The hard provider request limit is **100,000 characters in the final serialized JSON body**. Measure the exact body after provider-specific transformations and reject an oversized request before sending it.
- Track serialized characters, UTF-8 bytes, and model tokens as separate budgets. Never treat the character ceiling as a token limit.
- Compact only complete older turns when possible. Preserve the active user turn and required instructions. If required content alone exceeds a hard limit, fail clearly before the provider call.

## Document approval and revision integrity

- Approval binds `user_id`, `resource_id`, `base_revision`, canonical `patch_hash`, approver identity, and expiration. The current approval window is 15 minutes from preview creation and retries must not extend it.
- Auth Service establishes identity. Revision Service rechecks current resource permission, approval validity, base revision, and exact patch integrity at commit. Deny commit if current authorization cannot be verified.
- Never trust approval state supplied only by the LLM or AgentRuntime. Commit only the exact patch the authenticated approver reviewed.
- Stage artifacts as immutable checksum-addressed objects and verify stored bytes before publication. Do not assume PostgreSQL and object storage share a distributed ACID transaction.
- One database transaction is the publication boundary: record approval consumption, revision metadata, current-revision pointer, and outbox event together. Before commit, the revision is not visible. After commit, retries with the same idempotency key return the same revision.
- Recover abandoned staged objects after a safety interval. Fail closed and alert if a committed object's checksum or presence is invalid. Rollback creates a compensating revision and preserves history.

## Testing and verification

- Add focused regression tests for behavior changes. Run relevant backend tests from `packages/backend` with `python -m pytest tests/<relevant-test-files>`; run frontend tests from `packages/frontend` with `node node_modules/vitest/vitest.mjs run --passWithNoTests`.
- Use a disposable test database for migration and PostgreSQL integration tests. Never point test commands at production data. PostgreSQL-only tests require `MH_TEST_POSTGRES_URL` to reference a disposable test database.
- Verify security isolation, stale/revoked permissions, approval expiry, patch-hash mismatch, concurrent edits, commit retry, and rollback behavior for document changes.
- Verify runtime restart/recovery, event replay and gap handling, duplicate starts, cancel/resume, stale checkpoints, and unknown tool outcomes for runtime changes.
- Verify both request boundaries: 95,000-character preparation and 100,000-character serialized JSON. Confirm oversized requests are rejected before provider invocation.
- For Markdown-only work, validate internal links, fenced JSON, traceability, and whitespace; application tests are unnecessary unless behavior also changes.
- Run `git diff --check` before reporting completion. State exactly which tests ran and which gates remain unverified; a passing local test suite alone does not prove production readiness.

## Migration, staging, and production release

- For the approved fresh local baseline, recreate a versioned schema and remove legacy-only ORM/data adapters; no legacy backfill is required. Verify the exact local target before a separately scoped reset and validate fresh bootstrap, backup/restore, FK integrity, and product regressions in disposable PostgreSQL/staging. Production migration/data handling needs its own approved plan.
- Do not run irreversible production migrations, production cutovers, canaries, or remove an execution path without explicit production approval.
- Before a production rollout, pass the staging security, data-integrity, reliability, recovery, resource-exhaustion, backup/restore, and load gates. Zero unauthorized document access/commit, zero duplicate external side effects, and zero lost committed revisions are required.
- Measure the current runtime baseline before registering absolute performance thresholds. Keep those thresholds fixed before any runtime comparison.
- Evaluate DeerFlow only after the current runtime passes hard gates and the benchmark protocol is locked. Use the same model, tools, documents, prompts, workload, resource limits, scoring, and environment; randomize run order. Start with 50 paired tasks per workload group and three trials per task per runtime. Use paired 95% confidence intervals; review only at 50, 100, and at most 200 paired tasks per group. Inconclusive results at 200 are no-go.
- DeerFlow can qualify only when all hard and absolute performance gates pass, cost/run regression's 95% CI upper bound is at most 10%, and either task success improves by at least 10 percentage points with the CI lower bound meeting that gain, or p95 latency improves by at least 20% with CI confirmation while task success's CI lower bound is no worse than a 2-point decline. A passing benchmark permits a reviewed canary for new conversations; production cutover still requires explicit approval.
