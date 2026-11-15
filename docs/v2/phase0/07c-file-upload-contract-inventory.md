# Open WebUI file upload compatibility inventory — scoped Phase 0 draft

**Status:** Source-backed V1 consumer/producer characterization, not a complete Open WebUI contract audit or proof of V2 ingestion. The baseline checkout commit is recorded in [D07](07-open-webui-compatibility-manifest.md). The frontend consumer is `packages/frontend/src/lib/apis/files/index.ts`; the producer is `packages/backend/open_webui/routers/files.py`, mounted at `/api/v1/files` by `main.py`.

## Public contracts observed in this checkout

| Consumer | Wire request | Producer / success shape | Authorization and error behavior |
|---|---|---|---|
| `uploadFile` | `POST /api/v1/files/?process=<boolean>`; multipart `file` and optional JSON-string `metadata`; bearer token; `Accept: application/json` | `upload_file` returns a `FileModelResponse` including `id`, `filename`, owner, timestamps and metadata. It publishes `FILE_UPLOADED`. With background processing, the file ID can be returned while data status is `pending`. | `get_verified_user`; invalid metadata and disallowed extension produce 400, configured size excess produces 413. The frontend throws the response `detail` when present. |
| `getFileProcessStatus` | `GET /api/v1/files/{id}/process/status?stream=true`; bearer token | Non-stream response is `{ "status": ... }`. Stream is `text/event-stream` with `data: {"status":"pending|completed|failed", "error"?: ...}\n\n`; closes on `completed`/`failed`, and can emit `not_found` after opening. | Current file read access is checked at stream opening; missing/inaccessible file returns 404. The generator polls file state without rechecking permission on each event. The frontend returns a non-OK HTTP `Response` unchanged and its upload wrapper does not throw merely because a later status frame contains `error`. |
| `getFileContentById` | `GET /api/v1/files/{id}/content`; `credentials: include`, no bearer argument | Authenticated original bytes as `FileResponse`; Office bytes are served as attachment, while PDF is inline by default. | Current file read access is checked; missing/inaccessible file returns 404. Frontend converts success to `ArrayBuffer`. |

`uploadFile` invokes `onUploaded` as soon as the upload response exists, before it optionally awaits processing status. The UI must therefore distinguish “file ID received” from “processing complete” and from V2 parser/index coverage. The current processing stream does not emit a `[DONE]` marker from this producer; the frontend nevertheless recognizes that marker in its generic loop. An SSE `completed` event establishes only this V1 processing status, not `PARSER_COMPLETE`, `INDEX_COMPLETE` or document availability under [D05](05-coverage-semantics.md).

## Mini-Hermes attachment adoption boundary

`chatFileUploadController.ts` marks Office chat uploads with `mini_hermes_chat_upload: true`. The Open WebUI upload handler then attempts `register_chat_upload`, which copies supported Office bytes into the user's Mini-Hermes library. That copy is best effort: failure is logged while the Open WebUI upload remains successful; the agent tool may adopt the file lazily later. This is current behavior to preserve or explicitly version at the public upload boundary. It is **not** a valid V2 ingestion completion or immutable-revision publication guarantee. V2 must expose a separate durable ingestion state and evidence, with idempotent job creation and verified bytes before claiming a revision/index available. The existing file ID may be a source reference but is not itself a V2 document ID or coverage proof.

The `getFileContentById` call's computed URL was one of the 94 static pairing misses in D07; it has a concrete producer at `GET /{id}/content`. Dynamic URL construction, session credentials and actual browser cookie policy still need integration verification.

## Executed checks and remaining work

`packages/frontend/src/lib/apis/files/phase0-contract.test.ts` has **4 passing mocked-fetch cases** covering multipart/query/auth, callback timing without streaming, status URL/headers and error behavior, and cookie-based binary download. `packages/backend/tests/test_v2_phase0_file_api_contract.py` has **3 passing ASGI cases** with mocked upload handler/storage/identity: multipart DTO and upload event, status JSON/SSE and open-time access, and original-byte download with attachment disposition and 404 denial. These tests do not exercise real storage, database permissions, file size validation, background-worker restart, live cookie authentication, permission revocation during an open stream, or the V1-to-V2 adoption transaction.

Before freezing the V2 compatibility mapping, capture golden upload/status/error frames in a disposable local environment, test resumed processing and revoked permission during streaming, decide how V2 ingestion status is exposed without silently changing this Open WebUI API, and pair the actual chat UI callers with the proposed Document API V2 source-file registration contract. The Phase 2 vertical slice must prove the separate V2 state transition from uploaded bytes to verified revision and active index.

The proposed internal registration, first-revision and status semantics are now specified in [D03d](03d-ingestion-contract.md); its public endpoint mapping and implementation remain open.
