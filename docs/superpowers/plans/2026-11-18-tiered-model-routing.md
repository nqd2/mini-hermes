# Tiered Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admin-provisioned Free and Plus users three logical model choices each, with backend-only routing and exact per-request Plus limits.

**Architecture:** Open WebUI remains the identity and model-management owner; a Mini-Hermes policy layer publishes logical model IDs and resolves approved concrete provider models. PostgreSQL records entitlements and every physical inference dispatch. The current AgentRuntime and ProviderGateway remain the execution and validated transport paths.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async, PostgreSQL, pytest, Svelte, TypeScript, Vitest; existing OpenAI-compatible connections and OpenRouter API.

**Spec:** [Tiered model routing and release notes design](../specs/2026-11-18-tiered-model-routing-release-notes-design.md). This plan covers the tier/routing/quota subsystem; the independent [MH release plan](2026-11-18-mh-release-notes.md) covers release notes.

## Global Constraints

- Work in the supplied checkout; do not create a worktree. Keep `references/open-webui` unchanged
- Keep the current AgentRuntime as the production path, one immutable engine binding per conversation, and Revision Service as the only document commit authority.
- Keep the 95,000-character internal preparation limit and 100,000-character final serialized JSON limit, measured after provider transforms and before HTTP.
- Use existing stack/dependencies, PostgreSQL and SQLAlchemy async. Do not reset production data or run a production migration/cutover as part of this plan.
- Use a disposable PostgreSQL database for migration, locking, and concurrency tests (`MH_TEST_POSTGRES_URL`); never point tests at production.
- Preserve public Open WebUI API shapes; additive Mini-Hermes APIs and logical model IDs are allowed. No user-facing provider name, credential, or concrete model ID.
- Free uses only admin-approved OpenRouter `:free` models with verified zero pricing; Plus may use admin-configured OpenRouter, 1min, and OpenAI connections. No silent paid Free fallback.

## Review Focus

- A forged or stale raw model ID in a saved chat/API request must be denied for non-admins (Task 5 test).
- Two Plus calls competing for the final monthly unit must admit exactly one (Task 1 PostgreSQL test).
- Reordering or deleting a provider connection must invalidate the affected pool candidate without misrouting to another key (Task 3 test).
- Stale OpenRouter metadata or a mid-session price change must make a Free candidate unavailable (Task 4 test).
- A crash after dispatch is marked but before response must retain a consumed, reconcilable ledger row (Task 6 test).

---

### Task 1: Durable entitlement and dispatch ledger

**Files:** Create `packages/backend/open_webui/models/mini_hermes_tiers.py`, `packages/backend/open_webui/mini_hermes/tier_service.py`, `packages/backend/open_webui/mini_hermes/v2/models/tiers.py`, `packages/backend/open_webui/migrations/versions/f76a3b19c2d0_add_mh_model_tiers.py`, `packages/backend/open_webui/migrations_v2/versions/0006_mh_model_tiers.py`, `packages/backend/tests/test_mh_tier_ledger.py`; modify `packages/backend/open_webui/mini_hermes/models.py`, `packages/backend/open_webui/mini_hermes/v2/models/__init__.py`, and facade `packages/backend/open_webui/models/v2/schema.py`.

**Interfaces:** `TierRepository.get_plan(user_id: str) -> PlanSnapshot`; `set_plan(user_id: str, tier: Literal['free','plus'], monthly_request_limit: int | None, actor_id: str) -> PlanSnapshot`; `reserve_dispatch(context: DispatchContext, now: datetime) -> DispatchRecord`; `mark_dispatched(dispatch_key: str, now: datetime) -> DispatchRecord`; `release_before_dispatch(dispatch_key: str) -> None`; `get_usage(user_id: str, now: datetime) -> UsageSnapshot`. Define `PlanSnapshot`, `DispatchContext`, and `UsageSnapshot` in `tier_service.py` for later tasks. `get_plan` lazily creates a Free row for an existing active user inside a transaction, so concurrent first requests lock the same row.

- [ ] Write `test_default_free`, `test_plus_month_boundary`, `test_same_dispatch_key_is_idempotent`, `test_conflicting_dispatch_key_rejected`, `test_two_callers_one_remaining_unit`, and `test_dispatched_unknown_consumes_unit`; assert Free default, UTC calendar rollover, unique fingerprint, one winner, and counted `sent/unknown` states.
- [ ] Run `python -m pytest tests/test_mh_tier_ledger.py -q` from `packages/backend` with `MH_TEST_POSTGRES_URL` set to a disposable PostgreSQL DB; confirm the new behavior fails before implementation.
- [ ] Add `mh_user_plan`, `mh_llm_dispatch`, and `mh_run_model_selection` with FK to active `mh_ow_user.id`, unique dispatch key, unique run selection, UTC timestamps, and indexed user/month and Free-window queries. Add the `logical_model_id` column to the existing `mh_resource_task` table and ORM for durable background attribution. The V2 baseline includes `mh_ow_user`; both migration branches must target that identity table rather than V2's separate `mh_users`. Main migration follows `c7d2e8a9146b`; V2 migration follows `0005_v2_user_input_wait` and creates the same tables for fresh bootstrap. Lock the plan row during admission; for Free global windows use a PostgreSQL transaction advisory lock keyed by OpenRouter account and UTC window. Default Free caps: 2/user/day, 50/account/day, 20/account/minute; admin configuration may increase them. Admins are exempt from end-user quota but audited.
- [ ] Run the same test plus `tests/test_v2_bootstrap.py`; confirm both migration paths create the matching schema in separate disposable databases and all cases pass.
- [ ] Commit the schema, repository, and tests as one reviewable change.

### Task 2: Admin-provisioned accounts and plan APIs

**Files:** Create `packages/backend/open_webui/mini_hermes/tier_routes.py`, `packages/backend/tests/test_mh_tier_routes.py`; modify `packages/backend/open_webui/main.py`, `packages/backend/open_webui/routers/auths.py`, `packages/backend/open_webui/utils/oauth.py`, and the relevant auth config defaults in `packages/backend/open_webui/config.py`.

**Interfaces:** Consume `TierRepository`. `GET /api/v1/mini-hermes/me/plan` returns `{tier, monthly_request_limit, used, remaining, reset_at}`; admin `PUT /api/v1/mini-hermes/admin/users/{user_id}/plan` accepts `{tier, monthly_request_limit}`. Free requires `null`; Plus requires a positive integer. Error codes are `entitlement_missing`, `quota_exhausted`, `model_unavailable`, and `free_capacity_exhausted` with consistent HTTP status/detail.

- [ ] Write route tests for admin upgrade/downgrade, positive Plus cap, forbidden non-admin edit, Free default on admin-created account, pending-user inference denial, and existing-user OAuth login while unprovisioned OAuth signup fails.
- [ ] Run `python -m pytest tests/test_mh_tier_routes.py -q`; confirm expected failures.
- [ ] Add the tier router and server-side invite-only gate after first-admin bootstrap; retain local admin account creation and Google OAuth login for already provisioned accounts. Plan edits lock the same row as quota admission and publish the existing admin audit event pattern.
- [ ] Run the route tests plus `tests/test_production_auth_providers.py`; confirm pass.
- [ ] Commit account and API behavior.

### Task 3: Stable connection identities and approved pools

**Files:** Create `packages/backend/open_webui/mini_hermes/model_routing.py`, `packages/backend/tests/test_mh_model_pool.py`; modify `packages/backend/open_webui/routers/openai.py` and `packages/backend/open_webui/mini_hermes/tier_routes.py`.

**Interfaces:** `ModelPoolCandidate(connection_id: str, model_id: str, priority: int, capabilities: set[str], min_context: int, enabled: bool)`; `ModelRoutingConfig(version: int, groups: dict[str, list[ModelPoolCandidate]], free_user_daily_limit: int, free_account_daily_limit: int, free_account_minute_limit: int)`; `get_model_routing_config() -> ModelRoutingConfig`; `update_model_routing_config(config: ModelRoutingConfig, actor_id: str) -> ModelRoutingConfig`; `resolve_connection(connection_id: str) -> tuple[int, str, str, dict]`. Defaults are 2, 50, and 20 respectively. Store `connection_id` as a stable UUID in each existing `openai.api_configs` item; pool config uses a dedicated `mh.model_routing.v1` Config key, never list index.

- [ ] Write tests for six exact group IDs, malformed/duplicate candidates, missing connection, connection reorder, connection deletion, no credential in returned admin config, positive Free cap values, and pool version change on update.
- [ ] Run `python -m pytest tests/test_mh_model_pool.py -q`; confirm expected failures.
- [ ] Add stable connection IDs on config save and one-time normalization of configured connections lacking IDs; reject accidental ID reuse or cross-connection reassignment. Add admin `GET/PUT /api/v1/mini-hermes/admin/model-routing` and store versioned approved pools. A missing or disabled connection makes its candidate unavailable.
- [ ] Run pool tests plus `tests/test_v2_phase0_openai_proxy_contract.py`; confirm pass.
- [ ] Commit connection and pool configuration.

### Task 4: OpenRouter Free eligibility and deterministic model choice

**Files:** Modify `packages/backend/open_webui/mini_hermes/model_routing.py`; create `packages/backend/tests/test_mh_model_selection.py`.

**Interfaces:** `select_model(alias: str, payload: dict, user_id: str, run_id: str | None) -> SelectedModel`, where `SelectedModel` has logical ID, stable connection ID, concrete model ID, pool version, and required capability snapshot. `get_or_pin_run_selection(user_id: str, run_id: str, choice: SelectedModel) -> SelectedModel` stores the first selection in `mh_run_model_selection` (Task 1) and returns it on later inference in the run. `replace_run_selection_if_pristine(run_id: str, replacement: SelectedModel) -> SelectedModel` atomically switches a failed candidate only if the AgentRuntime event/tool ledger proves no visible output or external effect. An attributable background task without a run uses the user's Small group.

- [ ] Write tests for text, image, tool, structured-output and context requirements; admin priority/health ordering; same-run pinning; candidate fallback only before output/tool effects; zero-price and `:free` checks; stale catalog; and changed pricing. Assert `mh-free-*` cannot select paid candidates.
- [ ] Run `python -m pytest tests/test_mh_model_selection.py -q`; confirm expected failures.
- [ ] Fetch OpenRouter `/api/v1/models` with existing HTTP client, cache metadata for at most one hour, and fail closed on stale/unknown eligibility. Use only approved candidates; apply OpenRouter zero price ceiling in Free request payload where supported. Select within the requested group using declared capabilities/context and recent health, without an extra LLM classifier. Persist the first run selection and change it only with an atomic compare-and-swap after checking the AgentRuntime event/tool ledger proves no output or external effect occurred.
- [ ] Run selection tests; confirm pass and no external request in unit tests.
- [ ] Commit selection behavior.

### Task 5: Logical model catalog and server-side authorization

**Files:** Modify `packages/backend/open_webui/main.py`, `packages/backend/open_webui/utils/models.py`, `packages/backend/open_webui/routers/openai.py`, `packages/backend/open_webui/routers/chats.py`; create `packages/backend/tests/test_mh_virtual_model_access.py`.

**Interfaces:** Consume `TierRepository.get_plan` and `select_model`. `GET /api/models` and `GET /openai/models` return only the three logical models allowed by the non-admin user's tier. `POST /api/chat/completions`, `/openai/chat/completions`, and `/openai/responses` accept only an allowed logical ID for that user; existing chat history stays readable but old concrete IDs cannot initiate a new turn.

- [ ] Write tests for Free and Plus catalogs, pending users, forged/raw IDs, saved-chat continuation, API-key-authenticated calls, direct-connection attempts, and generic passthrough enabled by config; assert no provider credential or concrete ID reaches non-admin responses.
- [ ] Run `python -m pytest tests/test_mh_virtual_model_access.py -q`; confirm expected failures.
- [ ] Insert virtual models into the active model lookup without publishing concrete candidates to non-admins. Enforce entitlement at every inference entry and prohibit non-admin direct connections/passthrough; preserve admin model-management behavior. Render historical concrete model IDs as a generic legacy label for non-admins, without rewriting chat history.
- [ ] Run the new tests plus `tests/test_chat_model_resolution.py` and `tests/test_v2_phase0_model_api_contract.py`; confirm pass.
- [ ] Commit catalog and authorization.

### Task 6: Count each physical LLM dispatch at the gateway

**Files:** Modify `packages/backend/open_webui/agent_runtime/gateway.py`, `packages/backend/open_webui/routers/openai.py`; create `packages/backend/tests/test_mh_llm_dispatch.py`.

**Interfaces:** `ProviderGateway.send(..., llm_dispatch: DispatchContext | None = None, **request_options)`. Chat Completions and Responses require a server-created dispatch context; embeddings and non-LLM calls do not consume Plus units. The gateway reserves before transport, marks `sent` immediately before HTTP, releases only verified pre-dispatch failure, and retains `unknown` on network ambiguity. Each physical retry has a new dispatch key.

- [ ] Write tests for a streamed response counted once, tool-loop calls counted individually, provider 429/500 counted after dispatch, preparation rejection not counted, duplicate invocation idempotency, crash/unknown outcome retained, quota rejection before provider invocation, and 95,000/100,000-character boundaries after provider transforms.
- [ ] Run `python -m pytest tests/test_mh_llm_dispatch.py -q`; confirm expected failures.
- [ ] Thread authenticated user/run/group identity through the OpenAI-compatible chat and Responses routes into the selected model and gateway; make missing context fail closed for user-attributable inference. Record provider request ID/status and concrete model privately, return the logical model ID to end users, and preserve the existing wire-body validation order.
- [ ] Run the new tests plus `tests/test_agent_runtime_gateway.py`, `tests/test_agent_runtime_contract.py`, and `tests/test_v2_phase0_provider_proxy_limits.py`; confirm pass.
- [ ] Commit metered dispatch.

### Task 7: Background jobs, Free shared limits, and recovery

**Files:** Modify `packages/backend/open_webui/routers/tasks.py`, `packages/backend/open_webui/mini_hermes/service.py`, `packages/backend/open_webui/mini_hermes/routes.py`, `packages/backend/open_webui/mini_hermes/resource_tasks.py`, `packages/backend/open_webui/mini_hermes/document_analysis_tasks.py`, and `packages/backend/open_webui/mini_hermes/worker.py`; create `packages/backend/tests/test_mh_tier_background_calls.py`. Use the `mh_resource_task.logical_model_id` column added in Task 1.

**Interfaces:** Every user-triggered background inference carries durable `owner_id` and logical group to `ProviderGateway.send`. The ledger in Task 1 admits Free by per-user/day and shared account/day/minute caps; a shared-cap 429 stops further Free dispatch until reset. System-owned jobs use an explicit separate auditable system identity/budget.

- [ ] Write tests for title/follow-up, document description/summary, agent tool-loop attribution, Free user and shared caps, a 429 cooling only its candidate, shared-cap exhaustion, retry-after information, and recovery of an orphaned reservation that never reached transport.
- [ ] Run `python -m pytest tests/test_mh_tier_background_calls.py -q`; confirm expected failures.
- [ ] Replace hard-coded small-task model selection for attributable tasks with the originating logical group or tier Small default; persist owner/group with queued jobs. Ensure worker calls use the same route/quota policy and never borrow another user's grant. Keep unknown dispatch outcomes for reconciliation.
- [ ] Run new tests plus `tests/test_agent_runtime_runner.py` and `tests/test_document_analysis.py`; confirm pass.
- [ ] Commit background and Free-cap integration.

### Task 8: Admin and account UI

**Files:** Create `packages/frontend/src/lib/apis/mini-hermes/tiers.ts`, `packages/frontend/src/lib/apis/mini-hermes/tiers.test.ts`; modify `packages/frontend/src/lib/components/admin/Users/UserList/EditUserModal.svelte`, `packages/frontend/src/lib/components/chat/ModelSelector.svelte`, `packages/frontend/src/lib/components/chat/Settings/Usage.svelte`, and the admin model settings screen under `packages/frontend/src/lib/components/admin/Settings/Models.svelte`.

**Interfaces:** Use Task 2 plan endpoints and Task 3 pool endpoints. Non-admin chat selector shows exactly the three server-provided logical models; account usage shows remaining calls and reset time. Admin can edit tier/Plus cap and six approved pools without seeing any key material in API responses.

- [ ] Write Vitest tests for three choices per tier, hidden provider/model metadata, Plus cap edit validation, remaining count, quota-exhausted response, and admin pool save errors.
- [ ] Run `node node_modules/vitest/vitest.mjs run --passWithNoTests src/lib/apis/mini-hermes/tiers.test.ts` from `packages/frontend`; confirm expected failures.
- [ ] Add typed API client and UI controls; let the server catalog decide visibility, never filter only in the browser. Use existing Svelte/i18n styles and keep account role controls separate from tier controls.
- [ ] Run the new test and `node node_modules/vitest/vitest.mjs run --passWithNoTests`; confirm pass. Run `npm run check` and report any pre-existing errors separately.
- [ ] Commit UI and client behavior.

### Task 9: Full feature verification and operating guide

**Files:** Add focused operator instructions to `README.md` and update affected architecture/testing docs; add `packages/backend/tests/test_mh_tier_end_to_end.py` and any required frontend contract test.

**Interfaces:** No new interface; verify Tasks 1–8 as a complete feature. The new route is disabled for non-admin users until an approved pool and OpenRouter key exist; unavailable groups return explicit errors.

- [ ] Write an end-to-end test with disposable PostgreSQL, stub provider responses, a Free account, and a Plus account; assert six logical IDs are tier-filtered, Plus calls across two configured providers consume the same monthly limit, Free never reaches a paid model, and restart/retry does not double-charge.
- [ ] Run `python -m pytest tests/test_mh_tier_end_to_end.py -q` with `MH_TEST_POSTGRES_URL`; confirm expected failures before any final integration fix.
- [ ] Make only integration fixes required by the test; document admin key/pool setup, Free capacity assumptions, UTC reset, errors, privacy review, and migration/rollback steps.
- [ ] Run all focused backend tier tests, relevant AgentRuntime/auth/model tests, full frontend Vitest, `git diff --check`, and staging security/recovery/load gates. Report exact pass/fail results; a local suite alone does not authorize production rollout.
- [ ] Commit the verified guide and integration tests. Do not apply production migration, canary, or traffic cutover without separate approval.
