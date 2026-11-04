# Mini-Hermes

Mini-Hermes adds versioned document editing and automatic Google Drive synchronization to Open WebUI. The agent reads files and proposes edits through backend tools. A user previews and confirms a revision before the worker writes to Drive. Open WebUI's chat and streaming remain in the upstream backend and frontend.

## Layout

- `packages/frontend`: Svelte frontend, including **Workspace → Documents**.
- `packages/backend`: Open WebUI backend, document API, worker and restricted Google connector.
- `references/open-webui`: preserved upstream source for comparison.
- `compose.yaml`: one application image used by the app, worker and Google connector, plus PostgreSQL, Redis and SeaweedFS.

PostgreSQL holds document metadata, revisions, previews, per-user encrypted OAuth refresh tokens and sync jobs. SeaweedFS holds immutable Office/PDF bytes. Google Drive is an external copy. The agent has no Drive write tool; the worker syncs after a confirmed commit.

## Run the stack

1. Copy `.env.example` to `.env`. Replace all five secret values with independent random values. Set Google OAuth client ID and secret when Drive integration is needed. Register `MH_GOOGLE_REDIRECT_URI` as an authorized redirect URI in Google Cloud.
2. Run `docker compose up --build -d`.
3. Open `http://localhost:8080`, create the first user, then visit **Workspace → Documents**.

`docker compose logs -f app worker google-connector` shows migration, preview and sync activity. The app and worker use the same application image. The Google connector is reachable only on the Compose network and requires `MH_CONNECTOR_SECRET`.

## Run backend and frontend locally with SeaweedFS

Run `powershell -ExecutionPolicy Bypass -File scripts/dev-seaweedfs.ps1`. The pinned single-node image creates bucket `mini-hermes` at `http://127.0.0.1:8333` with access key `admin` and secret `secret`. Its Docker volume persists across restarts.

For local development, use Python 3.12 and Node 22 or newer. Install backend packages with:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r packages\backend\requirements-mini-hermes.lock
cd packages\frontend
npm ci --force
```

Start three terminals from `packages/backend` with `PYTHONPATH` set to that directory, `DATA_DIR` pointing to a writable local directory, `USE_SLIM_DOCKER=true`, and the S3 variables below. The app starts migrations before the worker. Run:

```powershell
python -m uvicorn open_webui.main:app --host 127.0.0.1 --port 8080
python -m open_webui.mini_hermes.worker
python -m uvicorn open_webui.mini_hermes.drive:app --host 127.0.0.1 --port 8090
```

Set `WEBUI_SECRET_KEY`, `MH_TOKEN_ENCRYPTION_KEY` and `MH_CONNECTOR_SECRET` to independent values in each terminal, along with `MH_CONNECTOR_URL=http://127.0.0.1:8090`, `STORAGE_PROVIDER=s3`, `S3_ENDPOINT_URL=http://127.0.0.1:8333`, `S3_BUCKET_NAME=mini-hermes`, `S3_ACCESS_KEY_ID=admin`, `S3_SECRET_ACCESS_KEY=secret`, and `S3_ADDRESSING_STYLE=path`. From `packages/frontend`, run `npm run dev -- --host 127.0.0.1`; it proxies API calls to port 8080.

For OAuth while using the local frontend, set and register `MH_GOOGLE_REDIRECT_URI=http://127.0.0.1:8080/api/v1/mini-hermes/drive/callback`. Open both frontend and callback with the same host name so the browser sends the OAuth nonce cookie.

## Document workflow

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
