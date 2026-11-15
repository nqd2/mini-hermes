# Chat start, transport and reconnect inventory — scoped Phase 0 draft

**Status:** Checkout-backed characterization of the existing Open WebUI/AgentRuntime boundary. This is not a full live transport audit or proof of V2 replay. It extends [D07](07-open-webui-compatibility-manifest.md) and preserves ADR-001/002: the current AgentRuntime remains the production execution path.

## Start and response ownership

`Chat.svelte` calls `generateOpenAIChatCompletion` in `src/lib/apis/openai/index.ts`, which posts JSON to `POST /api/chat/completions` with bearer and session cookie. `main.py.chat_completion` authenticates, checks model access and chat context, then, for the normal session/chat path, creates one task per assistant message/model. It returns JSON `{status: true, task_ids: [...], chat_id}` once those tasks are scheduled; execution may fail after HTTP 200, in which case the task handler persists a terminal runtime event where possible and emits a chat error. The response is an **acceptance acknowledgement**, not answer completion. Multi-model task IDs are not AgentRuntime event sequences.

The normal answer transport is Socket.IO `events`, not the `createOpenAITextStream` SSE parser. The latter consumes provider/OpenAI-like `data:` frames and `[DONE]` for other flows; its existing EOF-without-`[DONE]` behavior is covered separately in [D07](07-open-webui-compatibility-manifest.md). These two transports must not be conflated.

## Socket.IO and durable event boundary

`Chat.svelte.chatEventHandler` consumes a Socket.IO envelope with `chat_id`, `message_id` and nested `data.type`/`data.data`. It dispatches `chat:completion`, `response:completion`, `chat:message:error`, `chat:active`, `chat:tasks:cancel`, `source`, `citation`, tool interaction and other UI events. `routes/+layout.svelte` separately subscribes to `events` for background/global behavior. `AgentRuntime.wrap_event_emitter` persists dictionary client events before forwarding them, then adds `runtime_turn_id`, `runtime_event_id` and `runtime_event_sequence` to the forwarded event. Oversized persisted event payloads can be replaced by a `snapshot_required` marker. The run-level sequence includes event types beyond a single chat message.

The current frontend source contains no consumer of `runtime_event_sequence`, `runtime_event_id` or `/api/v1/agent/turns/{turn_id}/events`. `Chat.svelte` neither deduplicates by `(turn_id, sequence)` nor checks for gaps. A Socket.IO reconnect alone therefore does not establish lossless replay. This is a source observation, not evidence that an event was lost in a live session.

The backend exposes authenticated owner-scoped `GET /api/v1/agent/turns/{turn_id}`, `POST /api/v1/agent/turns/{turn_id}/cancel` and `GET /api/v1/agent/turns/{turn_id}/events?after_sequence=&limit=`. The replay DTO includes `status`, `first_available_sequence`, `latest_sequence`, `next_after_sequence`, `has_more`, `gap_detected`, `snapshot_required`, optional `snapshot` and ordered events (`id`, `sequence`, `type`, `payload`, timestamps). The ASGI characterization in `test_v2_phase0_runtime_replay_api.py` verifies selected wire fields, a 404 for an unowned turn, and owner forwarding for status/cancel using a mocked runtime; it does not prove a browser reconnect or a complete snapshot.

## Current reconnect paths

| Chat type | Frontend action on socket connect | Producer contract | Limit |
|---|---|---|---|
| Saved chat | `GET /api/tasks/chat/{chat_id}` through `getTaskIdsByChatId`; if no active task and an assistant leaf is pending, reload chat | `chat_tasks.py` returns `{task_ids}`; an inaccessible chat returns an empty list, and visible task IDs are filtered to the requesting user | Task presence and reloaded persisted chat do not reveal every missed durable event or its sequence. |
| Temporary chat | `POST /api/tasks/chat/{chat_id}/resume` with new `session_id` | Returns `{task_ids, streams}` for a valid temporary session; expired/unavailable returns 404 | Stream output snapshot is not a run-level replay cursor. |

The frontend start response contains task IDs and chat ID but no guaranteed turn ID. A forwarded runtime event carries a turn ID, yet a client that missed its first event may not know which turn to replay. The V2 integration contract must provide an authenticated, current-authorization mapping from chat/message/task to turn ID, or arrange durable turn creation before an explicitly versioned acceptance response includes it. This choice must be frozen with the chat UI and Runtime owners; a model-supplied ID is never authority. A saved conversation's engine/version binding and run owner must be checked on lookup, with no cross-runtime checkpoint resume.

## V2 compatibility and recovery obligations

- Preserve the existing chat start JSON and Socket.IO event types for existing consumers unless a reviewed versioned change updates them. Treat HTTP 200 and Socket.IO `chat:active` as acceptance/progress only.
- Persist before publish; expose the durable run ID and latest sequence through an authorized association. On reconnect, fetch status/snapshot and replay after the last contiguous sequence. Deduplicate `(run_id, sequence)`, detect gaps and reconcile them; never silently advance past a missing sequence. An oversized-event snapshot marker requires a usable snapshot, not an empty success claim.
- Reauthorization applies to replay, status and chat/task lookup. A user who loses chat permission must not learn event content from a previously held turn ID. Do not show a completed answer solely because a socket closed or the SSE parser yielded `done` on EOF.
- Test two tabs, changed active chat, multi-model runs, lost first event, duplicate delivery, event persisted but emit failed, reconnect during approval pause, revoked access, snapshot-required event, server restart and response accepted before run creation. Verify source/evidence/citation text refers to the same emitted tool page.

## Executed evidence and gaps

`packages/frontend/src/lib/apis/streaming/chat-runtime-boundary-phase0.test.ts` has **3 mocked-fetch cases** for chat start, saved-chat task lookup and temporary-chat resume. `packages/backend/tests/test_v2_phase0_chat_task_contract.py` has **2 ASGI cases** with identity/Redis/chat access mocked, covering current task filtering and temporary resume/404. The runtime replay route module has **3 ASGI cases** covering selected replay/status/cancel wire behavior with a mocked runtime. The existing SSE consumer tests cover another transport. These cases do not test a live Socket.IO client, actual Redis ownership, event delivery ordering, frontend deduplication, durable association lookup or V2 persistence. The full D07 compatibility manifest and Phase 0 gate remain open.
