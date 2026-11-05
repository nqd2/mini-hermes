# Mini-Hermes

Mini-Hermes adds document editing to Open WebUI. Agent-run scripts use a separate sandbox, and their saved artifacts stay in PostgreSQL and SeaweedFS for this phase; publishing those artifacts to Drive is deferred. The existing preview-and-confirm workflow can sync confirmed revisions to Drive. Open WebUI's chat and streaming remain in the upstream backend and frontend.

## Layout

- `packages/frontend`: Svelte frontend, including **Workspace → Documents**.
- `packages/backend`: Open WebUI backend, document API, worker and restricted Google connector.
- `references/open-webui`: preserved upstream source for comparison.
- `compose.yaml`: one application image used by the app, worker, Google connector and document sandbox, plus PostgreSQL, Redis and SeaweedFS.

PostgreSQL holds document metadata, revisions, previews, per-user encrypted OAuth refresh tokens and sync jobs. SeaweedFS holds immutable Office/PDF bytes. Agent sandbox saves stay in this local storage. In the existing confirmed-edit workflow, the worker syncs to Drive after a commit.

## Run the stack

1. Copy `.env.example` to `.env` (or fill the existing `.env`). Replace all six secret values with independent random values, including `MH_SANDBOX_TOKEN`. For 1min.AI, keep `OPENAI_API_BASE_URL=https://api.1min.ai/openai/v1` and put your 1min API key in `OPENAI_API_KEY`; do not append `/chat/completions`. Set Google OAuth client ID and secret when Drive integration is needed. Register `MH_GOOGLE_REDIRECT_URI` as an authorized redirect URI in Google Cloud.
2. Run `docker compose up --build -d`.
3. Open `http://localhost:8080`, create the first user, then visit **Workspace → Documents**.

`docker compose logs -f app worker google-connector sandbox` shows migration, preview, sync and sandbox activity. All four services use the same application image. The sandbox accepts document scripts over an internal Compose network with no published host port and requires `MH_SANDBOX_TOKEN`.

## Run backend and frontend locally with SeaweedFS

Run `powershell -ExecutionPolicy Bypass -File scripts/dev-seaweedfs.ps1`. The pinned single-node image creates bucket `mini-hermes` at `http://127.0.0.1:8333` with access key `admin` and secret `secret`. Its Docker volume persists across restarts.

For local development, use Python 3.12 and Node 22 or newer. Install backend packages with:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r packages\backend\requirements-mini-hermes.lock
cd packages\frontend
npm ci --force
cd ..\..
```

Copy `packages/backend/.env.example` to `packages/backend/.env` and `packages/frontend/.env.example` to `packages/frontend/.env`. Fill the backend secrets, including the password in `DATABASE_URL` and a separate `MH_SANDBOX_TOKEN`, LLM URL/key, and Google OAuth values when needed. The SeaweedFS script uses the S3 credentials in the backend template. Backend and worker automatically read `packages/backend/.env`; the dev runner passes the sandbox token to all backend processes; Vite reads `packages/frontend/.env`. The root `.env` is for Docker Compose only. The frontend uses `VITE_WEBUI_BACKEND_URL` for browser API calls; set backend `CORS_ALLOW_ORIGIN` to the frontend origin. Use the same host name in both URLs, such as `127.0.0.1`, so login cookies work across ports.

From the repository root, start the local dev stack with:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-dev.ps1
```

This starts pinned SeaweedFS and PostgreSQL containers, then runs the sandbox, backend, worker, connector and Vite locally. The local sandbox listens on `127.0.0.1:8765`; Compose provides the internal network isolation described above. PostgreSQL uses `postgres:17.6`, a named volume and port `127.0.0.1:5433`; `DATABASE_URL` in the backend env is the single source for its database credentials. The previous SQLite file remains at `.data/local/webui.db` and is not migrated into PostgreSQL. Pass `-SkipSeaweedFS` or `-SkipPostgres` when using an already running external service. Ctrl+C stops the app processes; the storage containers remain available. Python edits or changes to `packages/backend/.env` restart the sandbox, backend, connector and worker. Vite hot reloads frontend code and restarts for changes to `packages/frontend/.env`. The root `.env` only affects Compose after restarting its services.

To run components separately, first run `scripts/dev-seaweedfs.ps1` and `scripts/dev-postgres.ps1` from the repository root. Start the sandbox with its token, then the app so it runs migrations, followed by the worker and connector. Run each Python command from `packages/backend` in its own terminal; run the token assignment in the sandbox terminal before its command:

```powershell
$env:MH_SANDBOX_TOKEN = ((Get-Content .env | Where-Object { $_ -match '^MH_SANDBOX_TOKEN=' } | Select-Object -Last 1) -split '=', 2)[1]
..\..\.venv\Scripts\python.exe -m open_webui.mini_hermes.sandbox_service
..\..\.venv\Scripts\python.exe -m uvicorn open_webui.main:app --host 127.0.0.1 --port 8080 --loop none
..\..\.venv\Scripts\python.exe -m open_webui.mini_hermes.worker
..\..\.venv\Scripts\python.exe -m uvicorn open_webui.mini_hermes.drive:app --host 127.0.0.1 --port 8090 --loop none
```

From `packages/frontend`, run `.\node_modules\.bin\vite.cmd dev --host 127.0.0.1`. Browser API calls go directly to `VITE_WEBUI_BACKEND_URL`; `WEBUI_BACKEND_URL` remains the proxy target for legacy relative paths. Keep LLM and Google secrets in the backend env file. Open `http://127.0.0.1:5173` for the separate frontend; the backend listens on `http://127.0.0.1:8080`.

For OAuth while using the local frontend, set and register `MH_GOOGLE_REDIRECT_URI=http://127.0.0.1:8080/api/v1/mini-hermes/drive/callback`. Open both frontend and callback with the same host name so the browser sends the OAuth nonce cookie.

The LLM connection needs an API base URL, API key and model ID available from the provider. Open WebUI lists models from the configured endpoint; select a model with function tool support in the chat UI, such as `gpt-4.1-mini`. 1min.AI's [OpenAI-compatible endpoint](output/api/openai-compatible.md) supports text Chat Completions, streaming and model-dependent function tools. For GPT-5.6 and GPT-6 Sol/Luna tool turns, the backend sends `reasoning_effort: "none"`; strict function tools use `parallel_tool_calls: false`. It does not provide Responses, embeddings, image or audio endpoints through this URL. You can also manage the connection in **Admin Settings → Connections**. A connection already saved there takes precedence over later env changes; update it there if the new URL or key does not appear. The three filled `.env` files should not be committed.

## Document workflow

The agent sandbox uses the bundled `docx`, `pdf`, `pptx` and `xlsx` skill packs in `packages/backend/open_webui/mini_hermes/skills`. Its outputs are saved in SeaweedFS with metadata in PostgreSQL. This agent flow does not publish to Drive in the current phase.

Upload DOCX, XLSX, PPTX or PDF, or connect Drive and import a Docs, Sheets or Slides link. A Google-native file is exported as an Office revision in SeaweedFS while its original Drive file ID and version remain in PostgreSQL. PDF is read-only.

Uploads are limited to 20 MB. Google Drive's native-file export endpoint has a separate 10 MB limit, so larger Google Docs, Sheets or Slides files cannot be imported through this path.

An agent can use `list_editable_documents`, `read_editable_document` and `propose_document_edit`. The preview worker applies constrained operations and stores a diff. The user confirms the preview in Workspace → Documents. Commit atomically increments the revision and inserts one sync job. Retrying a commit returns the same revision and job. The sync worker checks the user's OAuth token, edit permission and Drive version, then updates the original Drive file and an Office companion for native files. Status is `pending`, `synced` or `failed`; failures retry with backoff, and users can request retry. Conflicts require reimporting the Drive file before another edit.

Docs and Slides native writes include Google's `requiredRevisionId` precondition. The Drive v3 media update used for Office files and the Sheets values calls do not have a documented revision precondition in these requests, so their version check is best-effort if another editor writes at the same instant. See [Drive files.update](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update), [Docs batchUpdate](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate) and [Slides batchUpdate](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/batchUpdate). For native Sheets, values are written as `RAW` to preserve literal strings; formula operations and style changes are not supported on the Google-native original.

Operations currently supported:

| Format | Operations |
| --- | --- |
| DOCX | `replace_text`, `append_paragraph`, `set_paragraph_style`, `set_table_cell` |
| XLSX | `set_cell`, `add_sheet`, `remove_sheet`, `append_row` |
| PPTX | `replace_text`, `set_shape_text`, `set_shape_fill`, `add_slide` |
| PDF | text extraction only |

Google-native originals support a narrower synchronized operation set: Docs text replacement and append; Sheets literal cell value, add sheet and append row; Slides text replacement and add slide. The Office companion reflects all supported internal operations for that type.

## Verification

From the repository root, set `PYTHONPATH` to the absolute `packages/backend` path and run `python -m pytest packages/backend/tests/test_document_edits.py packages/backend/tests/test_document_workflow.py`. From `packages/frontend`, `npm run build` checks the frontend; from `packages/backend`, `uv lock --check` checks the backend lockfile. The local dev flow can be exercised without Google credentials: upload, preview, commit and inspect a failed sync job, then connect OAuth and retry.
