# ADR-V2-001 — Isolated V2 persistence and continuous AgentRuntime

**Status:** Proposed; requires explicit review/acceptance. **Scope:** Mini-Hermes V2-R1.

## Context

The approved master roadmap chooses a clean V2 data baseline while retaining Open WebUI and the existing AgentRuntime as the only production execution path. Existing ADR-001 and ADR-002 stay accepted. Current Excel read behavior motivates a new Document Engine with bounded, structured results and verifiable coverage.

## Decision

1. V2 uses independent PostgreSQL database/schema allocation, object namespace and index namespace. V1 data, revisions, chats, approvals, privileged state, checkpoints and embeddings are never imported. Re-upload files into V2; do not reset or modify production V1.
2. Existing Mini-Hermes AgentRuntime remains the **sole production execution engine**. Internal refactoring/replatform is permitted subject to characterized semantics. Replacing the engine or swapping to DeerFlow is a separate ADR decision; relabeling another engine is insufficient.
3. Open WebUI retains UI, auth/session, RBAC, chat and model management, with unchanged client-visible semantics unless an explicitly approved versioned API transition updates all consumers.
4. Internal storage, document, tool, parser/chunker, worker, index and skill interfaces may be redesigned. No legacy persistence compatibility layer is required.
5. V2-R1 must support XLSX/DOCX/PPTX/PDF; PDF is read-only. XLSX has structured workbook source model. OCR/Ollama/legacy Office/OpenDocument are not release blockers.
6. Read/search tools must not silently enqueue or retry indexing; durable ingestion has its own explicit operation/worker state.
7. Mutations require the Revision Service's preview + current permission + approver identity + unexpired exact patch approval + atomic revision publication. Agent approval cannot substitute for document approval.
8. No production migration, canary, cutover, destructive reset or traffic change under this ADR. Staging and production rollout require subsequent explicit approval.

## Invariants

- Conversation engine binding immutable; run binding must match. No V1 checkpoint resume within V2.
- Event committed before streaming; sequence monotonic per run; no silent replay gaps.
- Unknown external tool side effect is quarantined rather than blindly retried.
- Verified immutable bytes exist before a committed revision becomes visible; one DB transaction publishes revision, consumes approval, advances pointer and writes outbox.
- Parser/index/analysis/summary/LLM-observed coverage are separate; summary is not a verbatim copy.
- Request budgets: 95,000 internal prepared characters; 100,000 serialized final JSON characters after provider transforms, measured pre-send.

## Alternatives rejected

- Full V1 persistence migration/backfill: explicitly out of scope.
- Second production agent engine: conflicts with ADR-001 without a superseding decision.
- Flatten Office files to a single unstructured text blob: loses cell/slide/page/source anchors.
- Automatically treating summary complete as LLM saw every cell: invalid evidence claim.

## Consequences / follow-ups

Requires fresh-bootstrap tests, new typed contracts, compatibility inventory, code ownership, reversible isolated staging. Future parser changes rebuild derived index builds from immutable source without changing source revisions. Actual go-live and V1 retirement remain separate decisions.

**Review required:** architecture owner, runtime owner, Open WebUI owner, security/storage owner. Reconcile nomenclature with accepted ADRs before marking Accepted.
