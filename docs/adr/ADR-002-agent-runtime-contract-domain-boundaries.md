# ADR-002: Agent runtime contract and domain service boundaries

**Status:** Accepted

**Related:** [ADR-001](ADR-001-agent-runtime-production-path.md)

## Ownership and contracts

| Service | Owns | Does not own |
|---|---|---|
| Auth Service / Open WebUI | Identity authentication and the authenticated principal; UI, RBAC, chat, model management | Document revision decisions |
| AgentRuntime | Conversation/run orchestration, one engine binding per conversation, run binding, agent approval pause/resume, checkpoints, durable run events, cancellation and recovery | Direct writes to document database or object storage; document approval or commit authority |
| Document API | `read`, `inspect`, `search`, `propose_edit`, `export`; request validation and user-facing document endpoints | Revision commit authority; a commit endpoint must delegate to Revision Service |
| Revision Service | Patch validation, diff preview, approval authorization, revision preconditions, atomic publication, rollback | Agent planning or model-provided authorization state |
| MCP Gateway | Tool discovery, connection lifecycle, credential ownership, invocation, isolation, timeout, rate limit, and audit | Skill registry or document revision state |
| Skill Runtime | Skill registry, versioning, loading, permissions, and sandboxed skill execution | MCP connection credentials or document commit state |
| Document workers and object storage | Durable document jobs, immutable staged objects, large-file artifacts and integrity checks | Making staged objects visible as committed revisions |

Identity is authenticated by Auth Service. Revision Service receives that principal and rechecks current resource authorization at commit. If current permissions cannot be verified, commit is denied. Agent or LLM-provided approval state is never authoritative.

## Persistence and interface refinement

Persistence may be replaced across all product domains, including Open WebUI identity, RBAC, chat and configuration, while the domain authorities above remain unchanged. PostgreSQL with SQLAlchemy async is the new baseline. Fresh local bootstrap does not import legacy chat history, backfill old ORM rows, or resume old checkpoints. User-facing imports use the new format and cannot restore approvals or privileged execution state.

Insert/flush parent records before event/checkpoint/outbox children, keeping each domain publication in one database transaction. Use real PostgreSQL FK/concurrency tests rather than treating SQLite-only results as production proof. Public UI contracts remain supported; additional runtime status/snapshot/command semantics and schema design are specified in the [use-case and test specification](../design/mini-hermes-use-cases-design-and-tests.md). New fields/states there are target behavior, not claims that the current checkout already implements them.

## Document approval

Document approval is distinct from AgentRuntime approval. AgentRuntime may pause or resume a run while a user approves an agent action. Revision Service alone decides whether a document patch may commit.

Approval binds `user_id`, `resource_id`, `base_revision`, canonical `patch_hash`, approver identity, and expiration. The approval window is 15 minutes from preview creation and is not refreshed by retries; an expired preview requires a new preview and approval. At commit, Revision Service checks current authorization, approval validity and identity, base revision, and patch integrity again. The committed patch must hash to the exact patch approved. Approval consumption and revision publication occur in the same database transaction.

## Runtime binding and lifecycle

`ConversationEngineBinding` contains immutable `engine_type` and `engine_version`. `RunBinding` contains `engine_type`, `engine_version`, `engine_run_id`, `engine_thread_id`, `tool_contract_version`, and `skill_snapshot_version`; it must match its conversation binding. A run has one execution owner. Canary and rollback affect only engine selection for new conversations. Checkpoints are not portable between engines.

Operations:

- `start`: creates or returns the run identified by an idempotency key; the same key and same request must not create a second run, while reuse with different input is rejected.
- `stream(after_sequence)`: reads durable events after a monotonically increasing per-run sequence.
- `cancel`: durably requests cancellation; retries are idempotent. A run with an unknown external side-effect outcome remains paused for reconciliation.
- `resume`: resumes only a recoverable checkpoint from the same run and binding; retries cannot execute a tool side effect twice.
- `get_status`: returns owner-scoped status, binding, checkpoint version, and latest durable event sequence without leaking request secrets.

Conversations are active or archived. Runs progress through queued, running, waiting-approval, recovering, and terminal states. Tool invocation uses a durable ledger and provider idempotency key. If an external side effect may have happened but its outcome is unknown, do not retry blindly.

## Durable event stream

Append each event to the durable log before publishing it. Sequence numbers increase monotonically within a run. `stream(after_sequence)` replays from this log; clients deduplicate by `(run_id, sequence)`. SSE is only transport and may lose events. After reconnect, clients compare status/snapshot sequence with their cursor, replay missing events, and use a snapshot when the log identifies a gap. Never silently skip a sequence gap. Retention follows the run/session retention policy.

## Atomic revision publication protocol

1. Write staged bytes to an immutable, checksum-addressed key. Verify the read-back checksum and durable-write behavior.
2. In one database transaction, recheck current permission, approval, patch hash, and base revision; insert revision/approval/outbox metadata and advance the current pointer.
3. Expose a revision only through committed metadata after that transaction succeeds. Do not rely on a distributed transaction with object storage.
4. Recover abandoned staged objects after a safety interval. Retain outbox events until delivered; use a stable event ID for retry and consumer deduplication.
5. Treat missing or corrupt objects referenced by committed metadata as an integrity incident and fail closed. Rollback is a new compensating revision.
