# ADR-001: Mini-Hermes production agent runtime path

**Status:** Accepted

**Scope:** Production architecture for 10–20 users during a rolling 30-day window.

## Decision

Mini-Hermes keeps its current AgentRuntime as the only production execution path. DeerFlow is a conditional replacement candidate and is not a default migration. Open WebUI remains the product UI and owns authentication, RBAC, chat, and model management. Keep its existing license and copyright notices. Track the rolling 30-day user count; revisit licensing or branding only if real usage creates a need.

Production-grade reliability, security, recovery, observability, and data-integrity requirements remain in scope. A 99.9% availability target is an internal objective, not a committed SLA.

## Fresh persistence baseline and provider scope

The latest approved product specification replaces persistence across both Mini-Hermes and Open WebUI using PostgreSQL and SQLAlchemy async. The local development database may be reset during the separately scoped implementation. No legacy schema/data backfill, chat-history import, or checkpoint conversion is required. Keep the existing product capabilities, current execution engine, authorization boundaries, notices, and public contracts still used by the UI. This local reset authorization does not apply to production data or traffic.

Ollama is excluded from the target, including provider/model selection, discovery, health/about probes, and embedding configuration. Optional image/audio/embedding/search capabilities require their own configured providers; OpenAI compatibility alone does not prove those capabilities.

Detailed target use cases, new persistence, planned tests, and evidence-classified audit findings are in the [Mini-Hermes specification](../design/mini-hermes-use-cases-design-and-tests.md). Presence of that specification does not establish implementation or staging gate completion.

## Request budget

- Internal request preparation limit: **95,000 characters**.
- Hard limit: **100,000 characters in the serialized JSON body** sent to a provider.
- Measure the final serialized body after all provider-specific transformations. Reject an oversized body before making a provider request.
- Keep token, byte, and serialized-character budgets separate. The 100,000-character limit is not a token limit.

## Revision publication

PostgreSQL and object storage do not share a distributed ACID transaction. Stage each proposed artifact as an immutable object under a checksum-bearing key and verify the stored bytes before publication. One database transaction rechecks current authorization, approval, patch integrity, and revision preconditions; inserts revision metadata and an outbox record; consumes approval; and advances the current-revision pointer. That commit is the visibility boundary. APIs expose only committed revision metadata whose object was verified before the transaction.

A crash before the transaction leaves an unreferenced staged object for delayed orphan cleanup. A crash after commit but before acknowledgement is retried with the same idempotency key and returns the same revision. Missing or mismatched committed objects fail closed and raise an operational alert. Rollback creates a compensating revision and preserves prior history.

## Conversation engine selection

Each conversation has one immutable `ConversationEngineBinding`. Every run has one `RunBinding` and one execution owner that matches the conversation binding. Canary and rollback change engine selection only for newly created conversations. Existing conversations and runs remain pinned; checkpoints cannot move between runtimes.

## Conditional DeerFlow evaluation

Evaluate DeerFlow only after the current runtime has passed hard security, integrity, and reliability gates and its performance baseline is recorded. Lock the benchmark protocol before comparing runtimes. Use the same model/profile, tools and versions, documents, prompts, workload, limits, rubric, and environment; randomize run order. Start with 50 paired tasks in each workload group and three trials per task per runtime. Calculate paired 95% confidence intervals for task success, p95 end-to-end latency, and cost per run. Review only at predeclared sample points: 50, 100, then at most 200 paired tasks per group. Increase the sample when confidence intervals cross a preregistered decision boundary or are wider than its margin. Inconclusive results at 200 are no-go.

DeerFlow can qualify only if every hard gate and locked absolute performance threshold passes, and either:

1. Task success improves by at least 10 percentage points, with the lower bound of its 95% CI also at least 10 points; or
2. P95 latency improves by at least 20%, its 95% CI confirms that reduction, and the lower bound of the task-success CI shows no regression greater than 2 percentage points.

For either route, the upper bound of the 95% CI for cost/run regression must be no more than 10%. A passing benchmark permits a reviewed canary for new conversations; it does not authorize production cutover. Explicit production approval is required. Rollback changes selection for new conversations only. Remove the old runtime path only after approved cutover and successful draining.

## Evidence classification

- **Verified in checkout:** the current AgentRuntime, document/revision services, and request preparation budgeting have implementations. Presence of code or tests does not establish production readiness.
- **Fixed assumptions:** 10–20 users per rolling 30 days; Open WebUI keeps its stated responsibilities; request caps are 95,000 preparation and 100,000 serialized JSON characters; current AgentRuntime remains the production path.
- **Must be established by tests and staging:** publication crash recovery, current authorization at commit, durable outbox replay, event-gap recovery, side-effect idempotency, all hard gates, and performance thresholds measured from baseline.
