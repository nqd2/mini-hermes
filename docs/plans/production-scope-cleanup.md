# Production scope cleanup

**Status:** Implementation in progress. This record captures the approved product choices; it does not certify production readiness.

The earlier [use-case inventory](../design/mini-hermes-use-cases-design-and-tests.md) contains test scenarios for retired Open WebUI products. Those scenarios are historical and do not gate this release. Retained features still require the hard staging gates in [the AgentRuntime plan](production-agent-implementation-plan.md).

## Product decisions

- Keep Open WebUI authentication, RBAC, chat and model management. Support local accounts and Google OAuth. Keep Google Drive import and revision sync, with separate credentials and scopes from sign-in.
- Keep Mini-Hermes documents, revisions, MCP Gateway, Skill Runtime, prompt templates, memory, operational analytics and essential notifications. Merge the separate Knowledge surface into the document library.
- Keep web search behind permission and provider configuration. Keep agent image generation for Office documents only after the user chooses it through `ask_user`.
- Make `ask_user` a durable AgentRuntime wait: no browser timeout, owner-scoped answer/cancel, idempotent resume, and recovery after reload or restart. This is distinct from tool and document approval.
- Keep local document-skill sandboxing. Remove external terminal, file browser and remote desktop integrations.
- Remove OneDrive, non-Google SSO/SCIM, Ollama, Notes, Channels, encrypted channels, comments, Calendar, Automations, Apps, audio, Playground, public/community chat sharing, user webhooks, Subagents, arena/leaderboard UI and legacy Functions/Pipelines execution after required capability moves to MCP/Skill Runtime.
- Preserve current Open WebUI license and copyright notices and rolling 30-day user tracking.

## Implementation sequence

1. Inventory dependencies and add behavior tests for removed and retained product surfaces.
2. Remove excluded frontend routes/controls and backend routes, flags, jobs and exclusive persistence models; keep the retained document, runtime and provider contracts.
3. Implement durable user-input interactions and explicit image-generation choice.
4. Align fresh PostgreSQL/SQLAlchemy persistence and versioned import/export with the reduced scope; update architecture and use-case documentation.
5. Run focused backend/frontend regression tests, `git diff --check`, and staging security, integrity, recovery, backup/restore and load gates. Production rollout remains separately approved.

No production reset, irreversible migration, or live cutover is authorized by this plan.

## Checkout progress (2026-11-16)

- Removed top-level routes and client feature advertisement for Apps, Automations, Calendar, Channels, comments, evaluations, Functions, Notes, Pipelines, terminal integration, and audio. Removed public chat-share and LDAP sign-in routes. Google is the only registered external sign-in provider.
- Removed the corresponding primary frontend pages and navigation, OneDrive upload controls, and public chat sharing controls. Google Drive upload and retained auth/chat/model/document/tool/skill routes remain available. The Documents library now has Files and permission-gated Collections tabs; the old Knowledge list URL redirects to Collections, and detail deep links remain available. Comment controls were removed from the Collections list and detail screens.
- Removed unused LDAP handlers and form, non-Google OAuth admin/provider configuration, and OneDrive configuration from the product defaults. The admin authentication screen now configures Google sign-in directly.
- Removed startup import of legacy `config.json` and legacy `rag.web` key renaming; fresh configuration is seeded from the registered defaults.
- Removed Ollama product defaults, config aliases, old database rename, model ID special cases, the unmounted backend router, unused request/response conversion helpers, model-file parser, and unused cloud-search adapter. Chat and embedding dispatch reject a stale Ollama-owned model before network access, including direct requests. Removed the frontend Ollama API client, admin management components, model-selector details, connection-modal branch, and advanced controls. Unused translation keys still require cleanup.
- Saved-chat `ask_user` validates answers and treats an identical answer or rejection retry as idempotent; it rejects a conflicting answer retry or a browser timeout. AgentRuntime now records a distinct `waiting_user_input` state and event for a saved question, and an owner can resume the same turn after runtime restart. Full browser reload and concurrent answer/cancel end-to-end gates remain outstanding.
- A disposable PostgreSQL 17 container is now available. Focused PostgreSQL tests passed for AgentRuntime (including restart/recovery), resource indexing, V2 migration/bootstrap, environment fail-closed checks, backup/restore, ingestion, index lifecycle, outbox fencing, and XLSX range pagination. Bootstrap now applies the full frozen 101-table release schema before seeding. The Python backup path includes runtime and Open WebUI tables, verifies row counts and foreign keys before committing a restore, and rolls back an invalid restore without losing target data. The fresh V2 migration excludes tables exclusive to retired Open WebUI features and no longer silently creates ORM tables on every upgrade. Revision 0005 updates existing AgentRuntime status constraints for `waiting_user_input`. Saved model flags can no longer advertise retired built-in agent tools. A combined local backend run passed 137 tests, the environment fail-closed suite passed 6 tests, and frontend Vitest passed 410 tests. The last frontend build succeeded. `svelte-check` still fails with 479 errors and 2 warnings across 129 files. The V2 M4 "staging" tests use SQLite and do not count as a staging gate.

The image-generation choice, remaining Knowledge detail URL and data integration, removal of comments and other retired controls throughout the rest of the frontend, remaining legacy model/config/entry-point removal, consolidation of duplicate persistence models, and real Linux staging gates are outstanding. Do not deploy this branch as the production cleanup.

The current AgentRuntime ORM declares `mh_agent_*` tables. The V2 baseline creates those tables in disposable PostgreSQL and now excludes retired runtime tables, but the V1 migration lineage still creates `agent_*`. Consolidate the approved fresh persistence baseline and remove legacy-only migration paths before treating this as production evidence.
