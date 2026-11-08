# ADR: Resource-aware agent boundaries

Status: accepted by the implementation plan.

## Ownership

The application orchestrates resource tasks. Resource services own versions,
permissions, extraction and provenance. PostgreSQL owns durable task checkpoints.
The sandbox owns temporary execution artifacts only. Open WebUI owns transport,
approval UI and the existing model tool loop.

Dependencies point from HTTP/UI adapters to application services and typed domain
contracts. Services receive ports rather than importing the FastAPI application.
Compatibility facades keep old tool names, signatures and import paths available.

## Execution

Resolve scope before creating/restoring state. Select requirements before reading.
Normalize tool effects, bound their observations, then record evidence actually
supplied to the model. Maintain independent requirements for compound requests.
Discovery and display never satisfy a content requirement. Final text for file
tasks is held until completion/partial/blocked status has been verified.

## Persistence and frontend

Task identity includes owner, chat and branch/assistant response identity. Resume
restores the same budget and rechecks scope. Compaction never becomes the source
of truth for resources or evidence. Frontend state is per chat/tab; pure reducers
consume backend events and scoped controllers own subscriptions and cancellation.

## Migration

Extract one responsibility at a time, retain compatibility exports, characterize
wire behavior, and keep correctness changes in separate commits. Rollbacks retain
original files, revisions, checkpoints and provider validation.
