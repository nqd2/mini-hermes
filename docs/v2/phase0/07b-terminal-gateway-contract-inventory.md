# Terminal gateway compatibility inventory — scoped Phase 0 draft

This inventory covers the existing terminal/tool-server frontend boundary. It is based on `packages/frontend/src/lib/apis/terminal/index.ts` and `packages/backend/open_webui/routers/terminals.py` in the working checkout. It does not certify the external terminal server's own API or the full Open WebUI compatibility manifest.

## Route ownership and discovery

`main.py` mounts the terminal router at `/api/v1/terminals`. `GET /api/v1/terminals/` is the Open WebUI server-list contract. It returns `[]` if tool servers are disabled and otherwise returns only enabled connections passing `has_connection_access`, with `id`, `url`, `name`, `contexts` and `config.chat_uploads`. The frontend `getTerminalServers` also returns `[]` when its feature flag is off or the request fails.

`resolveTerminalConnection` distinguishes a configured system server from a direct external server. For a system server, it constructs `/api/v1/terminals/{encoded server ID}`; for a direct server, it uses the external URL. The 22 unresolved `src/lib/apis/terminal/index.ts` calls in the static route-pairing index largely use this variable base URL. They are **not** 22 unmounted Open WebUI routes: the system-server branch reaches the gateway catch-all `/{server_id}/{path:path}`, while the direct branch bypasses Open WebUI. The direct branch requires a separate external-service contract and security review.

## Consumer to gateway mapping

| Frontend consumer | Method and relative upstream path | Open WebUI responsibility |
|---|---|---|
| `getTerminalFeatures`, `getTerminalConfig` | GET `/api/config` | Forward feature discovery through the selected system-server proxy when configured. Missing flags mean unsupported; frontend does not probe unsupported endpoints. |
| `getCwd`, `listFiles`, `searchFiles`, `getFileMatches`, `readFile`, `readComparisonSource`, download helpers | GET `/files/cwd`, `/files/list`, `/files/search` then `/files/glob` fallback, `/files/matches`, `/files/read`, `/files/view` | Forward query, identity and optional chat context; preserve JSON and binary responses. Source reads and downloads have different frontend size/error handling. |
| `archiveFromTerminal`, `uploadToTerminal`, `createDirectory`, `deleteEntry`, `setCwd`, `moveEntry` | POST `/files/archive`, `/files/upload`, `/files/mkdir`, `/files/cwd`, `/files/move`; DELETE `/files/delete` | Forward request method, body and Content-Type. These have external side effects and cannot be labeled retry-safe from the proxy method alone. |
| `getListeningPorts`, `getPortProxyUrl` | GET `/ports`, `/proxy/{port}/...` | Forward port requests; HTML under a port proxy receives link/history/fetch rewriting for the embedded browser. |
| Notebook helpers | POST `/notebooks`, `/notebooks/{id}/execute`; DELETE `/notebooks/{id}` | Forward notebook lifecycle and execution. External outcome on a lost connection may be unknown. |
| `terminalRequest`, skill and other callers | Dynamic method and path under selected base URL | Forward only after current connection access checks; each caller still needs its own payload/error inventory. |

## Authentication, session and error wire shape

`terminalRequest` sends `Authorization: Bearer {connection.key}` and, when a chat ID exists, `X-Session-Id`. It reads `{detail: string}` or `{error: string}` on failure, otherwise throws `Terminal request failed ({status})`. The gateway independently authenticates the Open WebUI user and checks live connection access. Its upstream `Authorization` is selected server-side from the configured bearer key, authenticated session, system OAuth token, or browser ticket; a client-provided bearer header is not forwarded as the system-server credential. It sets `X-User-Id`, forwards `X-Session-Id`, and may add a configured terminal context header. The configured chat context can require a saved chat, returning 409.

The gateway returns JSON `{error: ...}` for disabled servers (403), missing server (404), disabled or inaccessible connection (403), missing server URL (503), invalid path (400), blocked administration path (403), and upstream connection/timeout failure (502). Buffered upstream responses keep status and most headers; binary, image, PDF and `text/event-stream` responses stream. Browser WebSocket and port proxy paths have separate ticket and relay semantics.

## Executed characterization and remaining checks

`packages/frontend/src/lib/apis/terminal/phase0-contract.test.ts` covers feature-off behavior, selected proxy URL, bearer/chat headers and error-message fallback with mocked `fetch` (**3 passed**). `packages/backend/tests/test_v2_phase0_terminal_gateway_contract.py` mounts the actual router in ASGI, mocks identity/config/access and the upstream session, then checks server listing, forwarded identity/chat/upstream credential and blocked admin path (**2 passed**). These do not prove live RBAC, upstream path-specific behavior, WebSockets, streaming, browser tickets, client disconnects, external side-effect idempotency or V2 gateway compatibility. Those remain Phase 0 contract and later integration requirements.
