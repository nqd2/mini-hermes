<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { WEBUI_API_BASE_URL } from "$lib/constants";
  import OfficeDocumentPreview from "$lib/components/common/OfficeDocumentPreview.svelte";
  import DocumentViewerShell from '$lib/components/common/DocumentViewerShell.svelte';
  import XMark from '$lib/components/icons/XMark.svelte';

  export let chatId = "";
  type PreviewItem = {
    id: string;
    document_id: string;
    filename: string;
    status: string;
    diff: string | null;
    error: string | null;
    base_revision: number;
    base_sha256: string | null;
    artifact_sha256: string | null;
    patch_hash: string | null;
    revision?: number;
    viewer_url?: string;
    download_url?: string;
    sync_status?: string;
  };

  let previews: PreviewItem[] = [];
  let artifacts: PreviewItem[] = [];
  let visibleArtifacts: PreviewItem[] = [];
  let active: PreviewItem | null = null;
  let previewData: ArrayBuffer | null = null;
  let baseData: ArrayBuffer | null = null;
  let viewMode: "proposed" | "base" | "diff" = "proposed";
  let historyData: ArrayBuffer | null = null;
  let selectedHistoryRevision: number | null = null;
  let viewerPageCount = 0;
  let viewerCurrentPage = 1;
  let viewerTargetPage = 1;
  let viewerZoom: number | 'fit' = 'fit';
  let previewLoadId = "";
  let panelOpen = false;
  let loading = false;
  let committing = false;
  let downloading = false;
  let error = "";
  let commitMessage = "";
  let dismissedPreviewId = "";
  let latestPreviewSignature = "";
  let latestArtifactSignature = "";
  let timer: ReturnType<typeof setInterval>;
  let requestInFlight = false;

  $: visibleArtifacts = artifacts.filter(
    (artifact) =>
      !previews.some(
        (preview) =>
          preview.document_id === artifact.document_id &&
          preview.status === "committed" &&
          (preview.revision || preview.base_revision + 1) === artifact.revision,
      ),
  );

  const api = async (path: string, init: RequestInit = {}) => {
    const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${localStorage.token}`,
        ...init.headers,
      },
    });
    if (!response.ok)
      throw new Error(
        (await response.json().catch(() => ({}))).detail || response.statusText,
      );
    return response;
  };

  const refresh = async () => {
    if (
      !chatId ||
      chatId.startsWith("temporary:") ||
      chatId.startsWith("local:") ||
      requestInFlight
    )
      return;
    requestInFlight = true;
    try {
      const response = await api(
        `/chats/${encodeURIComponent(chatId)}/previews`,
      );
      previews = await response.json();
      const artifactResponse = await api(
        `/chats/${encodeURIComponent(chatId)}/artifacts`,
      ).catch(() => null);
      if (artifactResponse) {
        const records: Array<{
          document_id: string;
          filename: string;
          revision: number;
          viewer_url?: string;
          download_url?: string;
          sync_status?: string;
        }> = await artifactResponse.json();
        artifacts = records.map((record) => ({
          id: `artifact:${record.document_id}:${record.revision}`,
          document_id: record.document_id,
          filename: record.filename,
          status: "committed",
          diff: null,
          error: null,
          base_revision: Math.max(0, record.revision - 1),
          base_sha256: null,
          artifact_sha256: null,
          patch_hash: null,
          revision: record.revision,
          viewer_url: record.viewer_url,
          download_url: record.download_url,
          sync_status: record.sync_status,
        }));
      } else artifacts = [];
      if (
        active?.status === "committed" &&
        !previews.some((item) => item.id === active?.id)
      ) {
        if (!active.id.startsWith("artifact:"))
          previews = [active, ...previews];
      }
      const latest = previews[0] || null;
      const signature = latest ? `${latest.id}:${latest.status}` : "";
      const newResult =
        signature !== latestPreviewSignature &&
        latest &&
        ["ready", "committed"].includes(latest.status);
      latestPreviewSignature = signature;
      const latestArtifact =
        artifacts.find(
          (artifact) =>
            !previews.some(
              (preview) =>
                preview.document_id === artifact.document_id &&
                preview.status === "committed" &&
                (preview.revision || preview.base_revision + 1) === artifact.revision,
            ),
        ) || null;
      const artifactSignature = latestArtifact?.id || "";
      const newArtifact =
        artifactSignature !== latestArtifactSignature && !!latestArtifact;
      latestArtifactSignature = artifactSignature;
      if (newResult) {
        if (active?.id !== latest.id) {
          previewData = null;
          baseData = null;
          historyData = null;
          selectedHistoryRevision = null;
          previewLoadId = "";
          viewMode = "proposed";
          commitMessage = "";
        }
        active = latest;
        dismissedPreviewId = "";
        panelOpen = true;
      } else if (newArtifact && latestArtifact) {
        if (active?.id !== latestArtifact.id) {
          previewData = null;
          baseData = null;
          historyData = null;
          selectedHistoryRevision = null;
          previewLoadId = "";
          viewMode = "proposed";
          commitMessage = "";
        }
        active = latestArtifact;
        dismissedPreviewId = "";
        panelOpen = true;
      } else {
        active =
          previews.find((item) => item.id === active?.id) ||
          artifacts.find((item) => item.id === active?.id) ||
          previews.find((item) =>
            ["committed", "ready"].includes(item.status),
          ) ||
          latest ||
          latestArtifact;
      }
      if (!active) {
        panelOpen = false;
        previewData = null;
        previewLoadId = "";
      } else if (
        ["ready", "committed"].includes(active.status) &&
        previewLoadId !== active.id
      ) {
        void loadPreviewContent(active);
      }
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      requestInFlight = false;
    }
  };

  const loadPreviewContent = async (item: PreviewItem) => {
    previewLoadId = item.id;
    loading = true;
    try {
      const response = await api(
        item.status === "committed"
          ? `/documents/${item.document_id}/content?revision=${item.revision || item.base_revision + 1}`
          : `/previews/${item.id}/content`,
      );
      const content = await response.arrayBuffer();
      if (active?.id === item.id) previewData = content;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading = false;
    }
  };

  const showBaseRevision = async () => {
    if (!active || active.base_revision < 1) return;
    viewMode = "base";
    if (baseData) return;
    const item = active;
    loading = true;
    error = "";
    try {
      const response = await api(
        `/documents/${item.document_id}/content?revision=${item.base_revision}`,
      );
      const content = await response.arrayBuffer();
      if (active?.id === item.id) baseData = content;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading = false;
    }
  };

  const selectPreview = (item: PreviewItem) => {
    active = item;
    error = "";
    commitMessage = "";
    panelOpen = true;
    dismissedPreviewId = "";
    previewData = null;
    baseData = null;
    historyData = null;
    selectedHistoryRevision = null;
    viewMode = "proposed";
    previewLoadId = "";
    if (item.status === "ready" || item.status === "committed")
      void loadPreviewContent(item);
  };

  const closePanel = () => {
    panelOpen = false;
    if (active) dismissedPreviewId = active.id;
  };

  const selectHistoryRevision = async (number: number) => {
    if (!active) return;
    selectedHistoryRevision = number;
    historyData = null;
    error = '';
    try {
      const response = await api(`/documents/${active.document_id}/content?revision=${number}`);
      historyData = await response.arrayBuffer();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    }
  };

  const commit = async () => {
    if (!active || active.status !== "ready" || committing) return;
    committing = true;
    error = "";
    try {
      const approval = active.base_sha256 && active.artifact_sha256 && active.patch_hash
        ? active
        : await (await api(`/previews/${active.id}`)).json();
      const response = await api(`/previews/${active.id}/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_revision: approval.base_revision,
          base_sha256: approval.base_sha256,
          artifact_sha256: approval.artifact_sha256,
          patch_hash: approval.patch_hash,
        }),
      });
      const result = await response.json();
      commitMessage = "Saved as a new local revision.";
      active = {
        ...active,
        status: "committed",
        revision: result.revision,
        sync_status: result.sync_status,
      };
      previews = [active, ...previews.filter((item) => item.id !== active?.id)];
      panelOpen = true;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      committing = false;
    }
  };

  const retry = async () => {
    if (!active || active.status !== "failed") return;
    error = "";
    try {
      await api(`/previews/${active.id}/retry`, { method: "POST" });
      active = { ...active, status: "pending", error: null };
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    }
  };

  const download = async () => {
    if (!active) return;
    downloading = true;
    error = "";
    try {
      const response = await api(
        active.status === "committed"
          ? `/documents/${active.document_id}/content?revision=${selectedHistoryRevision || active.revision || active.base_revision + 1}`
          : `/previews/${active.id}/content`,
      );
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download =
        active.status === "committed"
          ? active.filename
          : `preview-${active.filename}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      downloading = false;
    }
  };

  onMount(() => {
    void refresh();
    timer = setInterval(() => void refresh(), 2500);
  });

  onDestroy(() => {
    clearInterval(timer);
  });
</script>

{#if (previews.length > 0 || artifacts.length > 0) && !panelOpen}
  <button
    class="fixed right-4 top-20 z-[90] rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm shadow-lg dark:border-gray-700 dark:bg-gray-900"
    on:click={() => (panelOpen = true)}
  >
    Files ({previews.length + visibleArtifacts.length})
  </button>
{/if}

{#if panelOpen && active}
  <DocumentViewerShell
    bind:zoom={viewerZoom}
    filename={active.filename}
    documentId={active.status === 'committed' ? active.document_id : null}
    revision={selectedHistoryRevision || active.revision || active.base_revision + 1}
    size={(selectedHistoryRevision && historyData ? historyData.byteLength : viewMode === 'base' && baseData ? baseData.byteLength : previewData?.byteLength) ?? null}
    accessLabel="Can edit"
    onClose={closePanel}
    onDownload={download}
    onRevisionSelect={selectHistoryRevision}
    onShowText={active.diff ? () => (viewMode = viewMode === 'diff' ? 'proposed' : 'diff') : null}
    textMode={viewMode === 'diff'}
    onShowOriginal={active.status === 'ready' && active.base_revision > 0 ? () => { if (viewMode === 'base') viewMode = 'proposed'; else void showBaseRevision(); } : null}
    originalMode={viewMode === 'base'}
    pageCountOverride={viewerPageCount}
    currentPageOverride={viewerCurrentPage}
    onPageSelect={(page) => (viewerTargetPage = page)}
    otherFiles={[...previews, ...visibleArtifacts].map((item) => ({ id: item.id, filename: item.filename }))}
    onFileSelect={(id) => {
      const item = [...previews, ...visibleArtifacts].find((entry) => entry.id === id);
      if (item) selectPreview(item);
    }}
  >
    <header
      class="hidden"
    >
      <div class="min-w-0 flex-1">
        <h2 class="truncate text-base font-semibold">
          {active.status === "committed" ? "Saved file" : "Xem trước bản sửa"}
        </h2>
        <p class="truncate text-xs text-gray-500">
          {active.filename} · revision {active.revision ||
            active.base_revision + 1}
        </p>
      </div>
      <button
        class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
        aria-label="Back to chat"
        on:click={closePanel}
        ><span class="md:hidden">←</span><span class="hidden md:block"
          ><XMark /></span
        ></button
      >
    </header>

    {#if previews.length + visibleArtifacts.length > 1}
      <nav
        class="hidden"
      >
        {#each previews as item (item.id)}
          <button
            class="max-w-64 truncate rounded-lg px-3 py-1.5 text-xs {active.id ===
            item.id
              ? 'bg-gray-100 dark:bg-gray-800'
              : 'text-gray-500'}"
            on:click={() => selectPreview(item)}
          >
            {item.filename}
          </button>
        {/each}
        {#each visibleArtifacts as item (item.id)}
          <button
            class="max-w-64 truncate rounded-lg px-3 py-1.5 text-xs {active.id ===
            item.id
              ? 'bg-gray-100 dark:bg-gray-800'
              : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-900'}"
            on:click={() => selectPreview(item)}
          >
            {item.filename} · r{item.revision}
          </button>
        {/each}
      </nav>
    {/if}

    {#if active.status === "ready" || active.status === "committed"}
      <nav
        class="hidden"
        aria-label="Document versions"
      >
        <button
          class="rounded-md px-3 py-1.5 {viewMode === 'proposed'
            ? 'bg-gray-200 dark:bg-gray-800'
            : 'hover:bg-gray-100 dark:hover:bg-gray-900'}"
          on:click={() => (viewMode = "proposed")}
          >{active.status === "committed"
            ? `Revision ${active.revision || active.base_revision + 1}`
            : "Proposed revision"}</button
        >
        {#if active.base_revision > 0}<button
            class="rounded-md px-3 py-1.5 {viewMode === 'base'
              ? 'bg-gray-200 dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-900'}"
            on:click={showBaseRevision}>Revision {active.base_revision}</button
          >{/if}
        {#if active.diff}<button
            class="rounded-md px-3 py-1.5 {viewMode === 'diff'
              ? 'bg-gray-200 dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-900'}"
            on:click={() => (viewMode = "diff")}>Text diff</button
          >{/if}
      </nav>
    {/if}

    <div class="min-h-full">
      {#if active.status === "pending" || active.status === "processing"}
        <p class="text-sm text-gray-500">Đang tạo preview…</p>
      {:else if active.status === "failed"}
        <div class="space-y-3">
          <p class="text-sm text-red-500">
            {active.error || "Không tạo được preview."}
          </p>
          <button
            class="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700"
            on:click={retry}>Thử chuẩn hóa operation và tạo preview lại</button
          >
        </div>
      {:else if active.status === "ready" || active.status === "committed"}
        {#if active.status === "committed"}
          <p
            class="sr-only"
            role="status"
          >
            {commitMessage || "Saved in your Personal Folder."}
          </p>
        {/if}
        {#if loading}
          <p class="p-4 text-sm text-gray-500">Đang tải nội dung tài liệu…</p>
        {:else if selectedHistoryRevision && historyData}
          <OfficeDocumentPreview filename={active.filename} data={historyData} bind:pageCount={viewerPageCount} bind:currentPage={viewerCurrentPage} targetPage={viewerTargetPage} zoom={viewerZoom} />
        {:else if viewMode === "diff"}
          <pre
            class="whitespace-pre-wrap break-words p-4 text-xs">{active.diff ||
              "Không có diff dạng văn bản."}</pre>
        {:else if viewMode === "base"}
          <OfficeDocumentPreview filename={active.filename} data={baseData} bind:pageCount={viewerPageCount} bind:currentPage={viewerCurrentPage} targetPage={viewerTargetPage} zoom={viewerZoom} />
        {:else}
          <OfficeDocumentPreview
            filename={active.filename}
            data={previewData}
            bind:pageCount={viewerPageCount}
            bind:currentPage={viewerCurrentPage}
            targetPage={viewerTargetPage}
            zoom={viewerZoom}
          />
        {/if}
      {/if}
      {#if error}<p class="p-4 text-sm text-red-500" role="alert">
          {error}
        </p>{/if}
    </div>

    <footer
      slot="action"
      class="flex shrink-0 justify-end gap-2 border-t border-white/10 bg-[#101010] p-3 {active.status === 'ready' ? '' : 'hidden'}"
    >
        {#if active.status === "ready"}
          <button
            class="rounded-lg bg-black px-4 py-2 text-sm text-white dark:bg-white dark:text-black disabled:opacity-50"
            disabled={committing}
            on:click={commit}
          >
            {committing ? "Đang lưu…" : "Chấp nhận và lưu"}
          </button>
        {/if}
    </footer>
  </DocumentViewerShell>
{/if}
