<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { WEBUI_API_BASE_URL } from "$lib/constants";
  import { user } from "$lib/stores";
  import { page } from "$app/stores";
  import { goto } from "$app/navigation";
  import Knowledge from "$lib/components/workspace/Knowledge.svelte";
  import { createLibraryLoader } from "$lib/components/workspace/documents/libraryLoader";

  $: showCollections = $page.url.searchParams.get("view") === "collections" &&
    ($user?.role === "admin" || $user?.permissions?.workspace?.knowledge);

  type DocumentItem = {
    id: string;
    filename: string;
    revision: number;
    drive_file_id: string | null;
    sync: { status: string; attempts: number; error: string | null } | null;
  };
  type PreviewItem = {
    id: string;
    status: string;
    diff: string | null;
    error: string | null;
    operations: object[];
    base_revision: number;
    base_sha256: string | null;
    artifact_sha256: string | null;
    patch_hash: string | null;
  };
  let documents: DocumentItem[] = [];
  let selected: DocumentItem | null = null;
  let previews: PreviewItem[] = [];
  let driveUrl = "";
  let findText = "";
  let replaceText = "";
  let sheetName = "Sheet";
  let cellAddress = "A1";
  let cellValue = "";
  let statusMessage = "";
  let busy = false;
  let loading = true;
  let loadingPreviews = false;
  let timer: ReturnType<typeof setInterval>;

  async function api(path: string, init: RequestInit = {}) {
    const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${localStorage.token}`,
        ...init.headers,
      },
    });
    if (!response.ok) {
      let body: any = {};
      try {
        body = await response.json();
      } catch {
        /* use status text */
      }
      throw new Error(body.detail || response.statusText);
    }
    return response.json();
  }

  const loader = createLibraryLoader<DocumentItem, PreviewItem>({
    selectedId: () => selected?.id ?? null,
    loadDocuments: () => api("/documents"),
    loadPreviews: (id) => api(`/documents/${id}/previews`),
    apply: (items, document, itemsPreviews) => {
      documents = items;
      selected = document;
      previews = itemsPreviews;
    },
    onError: (error) => {
      statusMessage = String(error);
    },
    onSettled: () => {
      loading = false;
      loadingPreviews = false;
    },
  });
  const refresh = () => loader.refresh();

  function selectDocument(document: DocumentItem | null) {
    selected = document;
    previews = [];
    loadingPreviews = !!document;
    void refresh();
  }

  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    busy = true;
    try {
      const data = new FormData();
      data.append("file", file);
      const item = await api("/documents/upload", {
        method: "POST",
        body: data,
      });
      await refresh();
      selectDocument(documents.find((doc) => doc.id === item.id) || null);
      statusMessage = "File uploaded and ready to review.";
    } catch (error) {
      statusMessage = String(error);
    } finally {
      busy = false;
      input.value = "";
    }
  }

  async function importDrive() {
    busy = true;
    try {
      const item = await api("/documents/import-drive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: driveUrl.trim() }),
      });
      await refresh();
      selectDocument(documents.find((doc) => doc.id === item.id) || null);
      statusMessage = "Drive file imported.";
    } catch (error) {
      statusMessage = String(error);
    } finally {
      busy = false;
    }
  }

  async function propose() {
    if (!selected) return;
    busy = true;
    try {
      const operations = selected.filename.toLowerCase().endsWith(".xlsx")
        ? [
            {
              op: "set_cell",
              sheet: sheetName,
              cell: cellAddress,
              value: cellValue,
            },
          ]
        : [{ op: "replace_text", find: findText, replace: replaceText }];
      await api(`/documents/${selected.id}/previews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operations }),
      });
      statusMessage = "Preview is being prepared.";
      await refresh();
    } catch (error) {
      statusMessage = String(error);
    } finally {
      busy = false;
    }
  }

  async function commit(preview: PreviewItem) {
    if (busy || !selected || preview.base_revision !== selected.revision)
      return;
    busy = true;
    try {
      const approval =
        preview.base_sha256 && preview.artifact_sha256 && preview.patch_hash
          ? preview
          : await api(`/previews/${preview.id}`);
      const result = await api(`/previews/${preview.id}/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_revision: approval.base_revision,
          base_sha256: approval.base_sha256,
          artifact_sha256: approval.artifact_sha256,
          patch_hash: approval.patch_hash,
        }),
      });
      statusMessage =
        result.sync_status === "local_only"
          ? "Revision saved. This file is stored locally."
          : "Revision saved. Google Drive sync queued.";
      await refresh();
    } catch (error) {
      statusMessage = String(error);
    } finally {
      busy = false;
    }
  }

  async function retry() {
    if (!selected || busy) return;
    busy = true;
    try {
      await api(`/documents/${selected.id}/sync/retry`, { method: "POST" });
      statusMessage = "Google Drive sync queued.";
      await refresh();
    } catch (error) {
      statusMessage = String(error);
    } finally {
      busy = false;
    }
  }

  async function connectDrive() {
    try {
      const result = await api("/drive/connect");
      window.location.href = result.authorization_url;
    } catch (error) {
      statusMessage = String(error);
    }
  }

  async function downloadCurrent() {
    if (!selected) return;
    const filename = selected.filename;
    try {
      const response = await fetch(
        `${WEBUI_API_BASE_URL}/mini-hermes/documents/${selected.id}/content`,
        {
          headers: { Authorization: `Bearer ${localStorage.token}` },
        },
      );
      if (!response.ok) throw new Error(response.statusText);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      statusMessage = String(error);
    }
  }

  async function downloadPreview(previewId: string) {
    if (!selected) return;
    const filename = selected.filename;
    try {
      const response = await fetch(
        `${WEBUI_API_BASE_URL}/mini-hermes/previews/${previewId}/content`,
        {
          headers: { Authorization: `Bearer ${localStorage.token}` },
        },
      );
      if (!response.ok) throw new Error(response.statusText);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `preview-${filename}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      statusMessage = String(error);
    }
  }

  onMount(() => {
    if (!showCollections) refresh();
    timer = setInterval(() => {
      if (!showCollections) void loader.refresh(true);
    }, 3000);
  });
  onDestroy(() => {
    clearInterval(timer);
    loader.dispose();
  });

  function openTab(view: "files" | "collections") {
    goto(
      view === "collections"
        ? "/workspace/documents?view=collections"
        : "/workspace/documents",
    );
    if (view === "files") refresh();
  }
</script>

<div class="mx-auto max-w-5xl p-6 text-gray-900 dark:text-gray-100">
  <h1 class="text-2xl font-semibold">Document library</h1>
  <nav class="mt-4 flex gap-2 border-b pb-2" aria-label="Document library">
    <button
      class="rounded-lg px-3 py-1.5 text-sm {showCollections
        ? 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'
        : 'bg-gray-100 font-semibold dark:bg-gray-800'}"
      aria-current={!showCollections ? "page" : undefined}
      onclick={() => openTab("files")}>Files</button
    >
    {#if $user?.role === "admin" || $user?.permissions?.workspace?.knowledge}
      <button
        class="rounded-lg px-3 py-1.5 text-sm {showCollections
          ? 'bg-gray-100 font-semibold dark:bg-gray-800'
          : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'}"
        aria-current={showCollections ? "page" : undefined}
        onclick={() => openTab("collections")}>Collections</button
      >
    {/if}
  </nav>
  {#if showCollections && ($user?.role === "admin" || $user?.permissions?.workspace?.knowledge)}
    <div class="mt-6"><Knowledge /></div>
  {:else}
    <p class="mt-1 text-sm text-gray-500">
      Upload Office or PDF files, or import a Google Docs, Sheets or Slides
      link. Agent edits appear as previews for your confirmation.
    </p>
    <div class="mt-6 flex flex-wrap items-center gap-3">
      <label
        class="cursor-pointer rounded-lg border focus-within:ring-2 focus-within:ring-blue-500 px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        Upload file <input
          class="sr-only"
          type="file"
          accept=".docx,.xlsx,.pptx,.pdf"
          onchange={upload}
          disabled={busy}
        />
      </label>
      <button
        onclick={connectDrive}
        class="rounded-lg border px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
        >Connect Google Drive</button
      >
    </div>
    <p class="mt-2 text-xs text-gray-500">
      DOCX, XLSX, PPTX or PDF · Up to 20 MB · PDFs are read-only
    </p>
    <div class="mt-4 flex gap-2">
      <input
        class="min-w-0 flex-1 rounded-lg border bg-transparent px-3 py-2 text-sm"
        aria-label="Google Docs, Sheets or Slides link"
        placeholder="Google Docs, Sheets or Slides link"
        bind:value={driveUrl}
      />
      <button
        class="rounded-lg bg-gray-900 px-4 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900"
        onclick={importDrive}
        disabled={busy || !driveUrl.trim()}>Import</button
      >
    </div>
    {#if statusMessage}<p class="mt-3 text-sm" role="status">
        {statusMessage}
      </p>{/if}
    <div class="mt-8 grid gap-6 md:grid-cols-[240px_1fr]">
      <div>
        <div class="mb-2 flex items-center justify-between gap-2">
          <h2 class="text-sm font-semibold">Your files</h2>
          <button class="text-xs underline" onclick={refresh} disabled={loading}
            >Refresh</button
          >
        </div>
        {#if loading}
          <p class="px-3 py-4 text-sm text-gray-500" role="status">
            Loading files…
          </p>
        {:else if documents.length === 0}
          <p class="rounded-lg border border-dashed p-4 text-sm text-gray-500">
            No files yet. Upload a DOCX, XLSX, PPTX or PDF to get started.
          </p>
        {/if}
        {#each documents as doc (doc.id)}
          <button
            class="mb-1 block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800 {selected?.id ===
            doc.id
              ? 'bg-gray-100 dark:bg-gray-800'
              : ''}"
            aria-pressed={selected?.id === doc.id}
            onclick={() => selectDocument(doc)}
          >
            <span class="block truncate">{doc.filename}</span><span
              class="text-xs text-gray-500">Revision {doc.revision}</span
            >
          </button>
        {/each}
      </div>
      {#if selected}
        <div class="space-y-6">
          <div class="rounded-xl border p-4">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h2 class="min-w-0 break-words font-semibold">
                {selected.filename}
              </h2>
              <button class="text-sm underline" onclick={downloadCurrent}
                >Download current revision</button
              >
            </div>
            <p class="mt-2 text-sm">
              {selected.drive_file_id
                ? `Drive sync: ${selected.sync?.status || "Not synced yet"}`
                : "Local file · Changes are saved here. Download a revision to share it."}
            </p>
            {#if selected.sync?.error}<p class="mt-1 text-sm text-red-600">
                {selected.sync.error}
              </p>{/if}
            {#if selected.sync?.status === "failed" && !selected.sync?.error?.includes("Drive conflict:") && !selected.sync?.error?.includes("no longer has edit access")}<button
                class="mt-2 rounded-lg border px-3 py-1 text-sm"
                onclick={retry}
                disabled={busy}>Retry sync</button
              >{/if}
          </div>
          {#if !selected.filename.toLowerCase().endsWith(".pdf")}
            <div class="rounded-xl border p-4">
              <h2 class="font-semibold">Propose an edit</h2>
              {#if selected.filename.toLowerCase().endsWith(".xlsx")}
                <div class="mt-3 grid gap-2 sm:grid-cols-3">
                  <label class="min-w-0 text-sm"
                    ><span class="mb-1 block text-gray-500">Sheet name</span
                    ><input
                      class="rounded-lg border bg-transparent px-3 py-2 text-sm"
                      aria-label="Sheet name"
                      placeholder="Sheet name"
                      bind:value={sheetName}
                    /></label
                  >
                  <label class="min-w-0 text-sm"
                    ><span class="mb-1 block text-gray-500">Cell (e.g. B2)</span
                    ><input
                      class="rounded-lg border bg-transparent px-3 py-2 text-sm"
                      aria-label="Cell (e.g. B2)"
                      placeholder="Cell (e.g. B2)"
                      bind:value={cellAddress}
                    /></label
                  >
                  <label class="min-w-0 text-sm"
                    ><span class="mb-1 block text-gray-500"
                      >Value or formula</span
                    ><input
                      class="rounded-lg border bg-transparent px-3 py-2 text-sm"
                      aria-label="Value or formula"
                      placeholder="Value or formula"
                      bind:value={cellValue}
                    /></label
                  >
                </div>
              {:else}
                <div class="mt-3 grid gap-2 sm:grid-cols-2">
                  <label class="min-w-0 text-sm"
                    ><span class="mb-1 block text-gray-500">Find</span><input
                      class="rounded-lg border bg-transparent px-3 py-2 text-sm"
                      aria-label="Find"
                      placeholder="Find"
                      bind:value={findText}
                    /></label
                  >
                  <label class="min-w-0 text-sm"
                    ><span class="mb-1 block text-gray-500">Replace with</span
                    ><input
                      class="rounded-lg border bg-transparent px-3 py-2 text-sm"
                      aria-label="Replace with"
                      placeholder="Replace with"
                      bind:value={replaceText}
                    /></label
                  >
                </div>
              {/if}
              <button
                class="mt-3 rounded-lg border px-3 py-2 text-sm"
                onclick={propose}
                disabled={busy ||
                  (selected.filename.toLowerCase().endsWith(".xlsx")
                    ? !sheetName || !cellAddress
                    : !findText)}>Create preview</button
              >
            </div>
          {/if}
          <div>
            <h2 class="mb-2 font-semibold">Previews</h2>
            {#if loadingPreviews}<p class="text-sm text-gray-500" role="status">
                Loading previews…
              </p>
            {:else if previews.length === 0}<p
                class="rounded-lg border border-dashed p-4 text-sm text-gray-500"
              >
                No previews yet. Propose an edit to review changes before
                saving.
              </p>{/if}
            {#each previews as preview (preview.id)}
              <div class="mb-3 rounded-xl border p-4">
                <p class="text-sm">{preview.status}</p>
                <pre
                  class="mt-2 overflow-auto text-xs text-gray-500">{JSON.stringify(
                    preview.operations,
                    null,
                    2,
                  )}</pre>
                {#if preview.diff}<pre
                    class="mt-2 max-h-64 overflow-auto rounded bg-gray-100 p-3 text-xs dark:bg-gray-800">{preview.diff}</pre>{/if}
                {#if preview.error}<p class="text-sm text-red-600">
                    {preview.error}
                  </p>{/if}
                {#if preview.status === "ready" || preview.status === "committed"}<button
                    class="mt-3 mr-3 text-sm underline"
                    onclick={() => downloadPreview(preview.id)}
                    >Download preview</button
                  >{/if}
                {#if preview.status === "ready" && preview.base_revision !== selected.revision}<p
                    class="mt-3 text-sm text-amber-700 dark:text-amber-400"
                  >
                    This preview is based on an older revision. Create a new
                    preview before saving.
                  </p>{/if}
                {#if preview.status === "ready"}<button
                    class="mt-3 rounded-lg bg-gray-900 px-3 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900"
                    onclick={() => commit(preview)}
                    disabled={busy ||
                      preview.base_revision !== selected.revision}
                    >Confirm and commit</button
                  >{/if}
              </div>
            {/each}
          </div>
        </div>
      {:else}
        <p class="rounded-xl border border-dashed p-6 text-sm text-gray-500">
          Select a file to download it, propose an edit, or review changes.
        </p>
      {/if}
    </div>
  {/if}
</div>
