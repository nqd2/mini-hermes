# Production agent implementation ledger

Plan: Mini-Hermes production: refactor full stack và hoàn thiện agent execution (user-approved 2026-11-08).

## Constraints

- Reuse main checkout per prior explicit user instruction; do not create worktrees.
- Preserve the unrelated uncommitted Windows asyncio change in internal/db.py and its two new files.
- Keep upload/chat/tool contracts, provider guards, PostgreSQL, SeaweedFS and existing worker.
- Separate behavior-preserving extraction commits from behavior changes.
- Task memory is chat/branch scoped. Conclusions are released only after evidence checks.

## Work ledger

- Stage 0: in progress — characterization, ADR, correctness regressions.
- Stage 1: pending — backend execution pipeline, normalized observations and answer gate.
- Stage 2: pending — indexed readers, format adapters, retrieval and sandbox lifecycle.
- Stage 3: pending — durable task checkpoints and long-task integration.
- Stage 4: pending — frontend controllers, reducers, request ownership and resource presentation.
- Stage 5: pending — remaining boundary extraction, CI and operational checks.

## Rulings

- Implement in the existing checkout rather than ask again about branch/worktree preferences.
- Use SQLAlchemy and existing worker for task persistence; no external memory framework.
- Characterize native wire contracts before extraction. A change is not complete merely because a helper passes tests.
- Baseline DB tests use a disposable file-backed SQLite URL locally; PostgreSQL-specific tests must report availability separately.

## Verification

Pending baseline.
