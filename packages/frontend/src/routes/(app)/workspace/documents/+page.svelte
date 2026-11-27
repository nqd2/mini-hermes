<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { toast } from "svelte-sonner";
  import { WEAI_API_BASE_URL } from "$lib/constants";
  import { config, user } from "$lib/stores";
  import { page } from "$app/stores";
  import { goto } from "$app/navigation";
  import Knowledge from "$lib/components/workspace/Knowledge.svelte";
  import { createLibraryLoader } from "$lib/components/workspace/documents/libraryLoader";
  import { pickDriveFiles } from "$lib/utils/google-drive-picker";
  import ArrowUpTray from "$lib/components/icons/ArrowUpTray.svelte";
  import ArrowDownTray from "$lib/components/icons/ArrowDownTray.svelte";
  import ArrowPath from "$lib/components/icons/ArrowPath.svelte";
  import Link from "$lib/components/icons/Link.svelte";
  import Search from "$lib/components/icons/Search.svelte";
  import PencilSquare from "$lib/components/icons/PencilSquare.svelte";
  import CheckCircle from "$lib/components/icons/CheckCircle.svelte";
  import DocumentPage from "$lib/components/icons/DocumentPage.svelte";
  import MaterialFileIcon from "$lib/components/common/MaterialFileIcon.svelte";
  import XMark from "$lib/components/icons/XMark.svelte";
  import ChevronDown from "$lib/components/icons/ChevronDown.svelte";

  $: showCollections =
    $page.url.searchParams.get("view") === "collections" &&
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
    operations: Record<string, any>[];
    base_revision: number;
    base_sha256: string | null;
    artifact_sha256: string | null;
    patch_hash: string | null;
  };
  type StatusBanner = {
    type: "success" | "error" | "info";
    message: string;
  };

  let documents: DocumentItem[] = [];
  let selected: DocumentItem | null = null;
  let previews: PreviewItem[] = [];
  let searchQuery = "";
  let showLinkImport = false;
  let driveUrl = "";
  let findText = "";
  let replaceText = "";
  let sheetName = "Sheet";
  let cellAddress = "A1";
  let cellValue = "";
  let statusBanner: StatusBanner | null = null;
  let driveConnected = false;
  let busy = false;
  let loading = true;
  let loadingPreviews = false;
  let timer: ReturnType<typeof setInterval>;

  $: filteredDocuments = searchQuery.trim()
    ? documents.filter((doc) =>
        doc.filename.toLowerCase().includes(searchQuery.trim().toLowerCase()),
      )
    : documents;

  const inputClass =
    "w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-900 outline-hidden transition-colors placeholder:text-gray-400 focus:border-blue-500 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-gray-100 dark:placeholder:text-gray-500 dark:focus:border-blue-400";
  const secondaryButtonClass =
    "inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08] dark:hover:text-white";
  const primaryButtonClass =
    "inline-flex items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-3.5 py-2 text-xs font-medium text-white shadow-xs transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-gray-950 dark:hover:bg-gray-200";

  function notify(type: StatusBanner["type"], message: string) {
    statusBanner = { type, message };
    if (type === "success") toast.success(message);
    else if (type === "error") toast.error(message);
    else toast.info(message);
  }

  function errorMessage(error: unknown): string {
    if (error instanceof Error) return error.message;
    return String(error);
  }

  function statusBadgeMeta(status: string): { label: string; className: string } {
    switch (status) {
      case "ready":
        return {
          label: "Ready to review",
          className:
            "bg-blue-500/10 text-blue-700 ring-1 ring-inset ring-blue-500/20 dark:bg-blue-500/15 dark:text-blue-300",
        };
      case "committed":
      case "synced":
        return {
          label: status === "synced" ? "Synced" : "Committed",
          className:
            "bg-emerald-500/10 text-emerald-700 ring-1 ring-inset ring-emerald-500/20 dark:bg-emerald-500/15 dark:text-emerald-300",
        };
      case "pending":
      case "processing":
        return {
          label: "Processing",
          className:
            "bg-amber-500/10 text-amber-700 ring-1 ring-inset ring-amber-500/20 dark:bg-amber-500/15 dark:text-amber-300",
        };
      case "failed":
        return {
          label: "Failed",
          className:
            "bg-rose-500/10 text-rose-700 ring-1 ring-inset ring-rose-500/20 dark:bg-rose-500/15 dark:text-rose-300",
        };
      default:
        return {
          label: status || "Unknown",
          className:
            "bg-gray-500/10 text-gray-700 ring-1 ring-inset ring-gray-500/20 dark:bg-white/10 dark:text-gray-300",
        };
    }
  }

  function describeOperation(op: Record<string, any>): string {
    if (!op || typeof op !== "object") return "Document update";
    switch (op.op) {
      case "replace_text":
        return `Replace "${op.find ?? ""}" → "${op.replace ?? ""}"`;
      case "set_cell":
        return `Set ${op.sheet || "Sheet"}!${op.cell || "A1"} = "${op.value ?? ""}"`;
      case "append_paragraph":
        return `Append paragraph: "${String(op.text ?? "").slice(0, 80)}"`;
      case "add_slide":
        return "Add new slide";
      case "add_sheet":
        return `Add sheet "${op.name ?? ""}"`;
      case "append_row":
        return `Append row to ${op.sheet || "Sheet"}`;
      case "sandbox_publish":
        return "Apply generated document revision";
      default:
        return op.op ? String(op.op).replaceAll("_", " ") : "Document update";
    }
  }

  async function api(path: string, init: RequestInit = {}) {
    const response = await fetch(`${WEAI_API_BASE_URL}/agent-runtime${path}`, {
      ...init,
      credentials: "include",
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
      notify("error", errorMessage(error));
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
      notify("success", `Uploaded "${file.name}" and ready to review.`);
    } catch (error) {
      notify("error", errorMessage(error));
    } finally {
      busy = false;
      input.value = "";
    }
  }

  async function importDrive() {
    if (!driveUrl.trim() || busy) return;
    busy = true;
    try {
      const item = await api("/documents/import-drive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: driveUrl.trim() }),
      });
      await refresh();
      selectDocument(documents.find((doc) => doc.id === item.id) || null);
      driveUrl = "";
      showLinkImport = false;
      notify("success", `Imported "${item.filename || "Drive file"}" from Google Drive.`);
    } catch (error) {
      notify("error", errorMessage(error));
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
      notify("info", "Preview is being prepared.");
      await refresh();
    } catch (error) {
      notify("error", errorMessage(error));
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
      notify(
        "success",
        result.sync_status === "local_only"
          ? "Revision saved locally."
          : "Revision saved and Google Drive sync queued.",
      );
      await refresh();
    } catch (error) {
      notify("error", errorMessage(error));
    } finally {
      busy = false;
    }
  }

  async function retry() {
    if (!selected || busy) return;
    busy = true;
    try {
      await api(`/documents/${selected.id}/sync/retry`, { method: "POST" });
      notify("info", "Google Drive sync queued.");
      await refresh();
    } catch (error) {
      notify("error", errorMessage(error));
    } finally {
      busy = false;
    }
  }

  async function loadDriveStatus() {
    try {
      const status = await api("/drive/status");
      driveConnected = Boolean(status?.connected);
    } catch {
      driveConnected = false;
    }
  }

  async function pickFromDrive() {
    if (busy) return;
    busy = true;
    try {
      const picked = await pickDriveFiles({ multiselect: false });
      if (!picked || picked.length === 0) return;
      const file = picked[0];
      const item = await api("/documents/import-drive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: file.url || file.id }),
      });
      await refresh();
      selectDocument(documents.find((doc) => doc.id === item.id) || null);
      notify("success", `Imported "${file.name}" from Google Drive.`);
    } catch (error) {
      notify("error", errorMessage(error));
    } finally {
      busy = false;
    }
  }

  async function connectDrive() {
    try {
      const result = await api("/drive/connect");
      window.location.href = result.authorization_url;
    } catch (error) {
      notify("error", errorMessage(error));
    }
  }

  async function downloadCurrent() {
    if (!selected) return;
    const filename = selected.filename;
    try {
      const response = await fetch(
        `${WEAI_API_BASE_URL}/agent-runtime/documents/${selected.id}/content`,
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
      notify("error", errorMessage(error));
    }
  }

  async function downloadPreview(previewId: string) {
    if (!selected) return;
    const filename = selected.filename;
    try {
      const response = await fetch(
        `${WEAI_API_BASE_URL}/agent-runtime/previews/${previewId}/content`,
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
      notify("error", errorMessage(error));
    }
  }

  onMount(() => {
    void loadDriveStatus();
    if ($page.url.searchParams.get("drive") === "connected") {
      notify("success", "Google Drive connected.");
      void goto("/workspace/documents", { replaceState: true });
    }
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

<div class="mx-auto w-full max-w-6xl px-4 py-5 text-gray-900 sm:px-6 dark:text-gray-100">
  <div class="flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-gray-900 dark:text-white">
        Document library
      </h1>
      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
        Upload Office or PDF files, or import from Google Drive. Agent edits appear as previews for your confirmation.
      </p>
    </div>

    <nav
      class="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5 dark:border-white/[0.08] dark:bg-white/[0.03]"
      aria-label="Document library"
    >
      <button
        class="rounded-md px-3 py-1.5 text-xs font-medium transition {!showCollections
          ? 'bg-white text-gray-900 shadow-xs dark:bg-white/[0.1] dark:text-white'
          : 'text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white'}"
        aria-current={!showCollections ? "page" : undefined}
        onclick={() => openTab("files")}>Files</button
      >
      {#if $user?.role === "admin" || $user?.permissions?.workspace?.knowledge}
        <button
          class="rounded-md px-3 py-1.5 text-xs font-medium transition {showCollections
            ? 'bg-white text-gray-900 shadow-xs dark:bg-white/[0.1] dark:text-white'
            : 'text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white'}"
          aria-current={showCollections ? "page" : undefined}
          onclick={() => openTab("collections")}>Collections</button
        >
      {/if}
    </nav>
  </div>

  {#if showCollections && ($user?.role === "admin" || $user?.permissions?.workspace?.knowledge)}
    <div class="mt-6"><Knowledge /></div>
  {:else}
    <!-- Action Toolbar -->
    <div
      class="mt-5 rounded-xl border border-gray-200/80 bg-gray-50/70 p-3 dark:border-white/[0.08] dark:bg-white/[0.02]"
    >
      <div class="flex flex-wrap items-center justify-between gap-3">
        <!-- Source actions -->
        <div class="flex flex-wrap items-center gap-2">
          <label
            class="{primaryButtonClass} cursor-pointer focus-within:ring-2 focus-within:ring-blue-500/60 {busy
              ? 'pointer-events-none opacity-50'
              : ''}"
          >
            <ArrowUpTray className="size-3.5" />
            <span>Upload file</span>
            <input
              class="sr-only"
              type="file"
              accept=".docx,.xlsx,.pptx,.pdf"
              onchange={upload}
              disabled={busy}
            />
          </label>

          {#if $config?.features?.enable_google_drive_integration && $config?.google_drive?.api_key}
            <button
              type="button"
              onclick={pickFromDrive}
              disabled={busy}
              class={secondaryButtonClass}
            >
              <svg class="size-3.5 shrink-0" viewBox="0 0 87.3 78" aria-hidden="true">
                <path
                  d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"
                  fill="#0066da"
                />
                <path
                  d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"
                  fill="#00ac47"
                />
                <path
                  d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z"
                  fill="#ea4335"
                />
                <path
                  d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"
                  fill="#00832d"
                />
                <path
                  d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"
                  fill="#2684fc"
                />
                <path
                  d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"
                  fill="#ffba00"
                />
              </svg>
              <span>Pick from Google Drive</span>
            </button>
          {/if}

          <button
            type="button"
            onclick={() => (showLinkImport = !showLinkImport)}
            aria-expanded={showLinkImport}
            class="inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition {showLinkImport
              ? 'border-blue-500/40 bg-blue-500/10 text-blue-700 dark:border-blue-400/40 dark:bg-blue-500/15 dark:text-blue-300'
              : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08] dark:hover:text-white'}"
          >
            <Link className="size-3.5" />
            <span>Import from link</span>
          </button>
        </div>

        <!-- Google Drive connection status -->
        <div class="flex flex-wrap items-center gap-2">
          {#if driveConnected}
            <span
              class="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300"
              role="status"
            >
              <span class="size-1.5 rounded-full bg-emerald-500"></span>
              Google Drive connected
            </span>
            <button
              type="button"
              onclick={connectDrive}
              class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-gray-300 dark:hover:bg-white/[0.08] dark:hover:text-white"
              title="Switch or re-authorize your Google Drive account"
            >
              <ArrowPath className="size-3" />
              <span>Reconnect</span>
            </button>
          {:else}
            <button
              type="button"
              onclick={connectDrive}
              class={secondaryButtonClass}
            >
              <svg class="size-3.5 shrink-0" viewBox="0 0 87.3 78" aria-hidden="true">
                <path
                  d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"
                  fill="#0066da"
                />
                <path
                  d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"
                  fill="#00ac47"
                />
                <path
                  d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z"
                  fill="#ea4335"
                />
                <path
                  d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"
                  fill="#00832d"
                />
                <path
                  d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"
                  fill="#2684fc"
                />
                <path
                  d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"
                  fill="#ffba00"
                />
              </svg>
              <span>Connect Google Drive</span>
            </button>
          {/if}
        </div>
      </div>

      {#if showLinkImport}
        <form
          class="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-200/80 pt-3 dark:border-white/[0.06]"
          onsubmit={(event) => {
            event.preventDefault();
            void importDrive();
          }}
        >
          <div class="relative min-w-0 flex-1">
            <div class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-gray-400 dark:text-gray-500">
              <Link className="size-3.5" />
            </div>
            <input
              class="{inputClass} pl-8"
              aria-label="Google Docs, Sheets or Slides link"
              placeholder="Paste a Google Docs, Sheets, Slides, or Drive file link…"
              bind:value={driveUrl}
            />
          </div>
          <button
            type="submit"
            class={primaryButtonClass}
            disabled={busy || !driveUrl.trim()}
          >
            Import
          </button>
          <button
            type="button"
            onclick={() => (showLinkImport = false)}
            aria-label="Close link import"
            class="inline-flex size-8 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-200/70 hover:text-gray-700 dark:text-gray-500 dark:hover:bg-white/[0.06] dark:hover:text-gray-200"
          >
            <XMark className="size-4" />
          </button>
        </form>
      {/if}

      <p class="mt-2.5 text-[11px] text-gray-500 dark:text-gray-400">
        DOCX, XLSX, PPTX, or PDF · Up to 20 MB · PDFs are read-only
      </p>
    </div>

    {#if statusBanner}
      <div
        role="status"
        class="mt-3 flex items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5 text-xs {statusBanner.type === 'error'
          ? 'border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-300'
          : statusBanner.type === 'success'
            ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
            : 'border-blue-500/25 bg-blue-500/10 text-blue-700 dark:text-blue-300'}"
      >
        <span class="min-w-0 break-words">{statusBanner.message}</span>
        <button
          type="button"
          onclick={() => (statusBanner = null)}
          aria-label="Dismiss notification"
          class="shrink-0 rounded-md p-0.5 opacity-70 transition hover:opacity-100"
        >
          <XMark className="size-3.5" />
        </button>
      </div>
    {/if}

    <div class="mt-5 grid gap-5 lg:grid-cols-[280px_1fr]">
      <!-- File List Sidebar -->
      <aside
        class="rounded-xl border border-gray-200/80 bg-white/70 p-3 dark:border-white/[0.08] dark:bg-white/[0.02]"
      >
        <div class="mb-2.5 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <h2 class="text-xs font-semibold text-gray-800 dark:text-gray-200">
              Your files
            </h2>
            <span
              class="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-600 dark:bg-white/[0.08] dark:text-gray-300"
            >
              {documents.length}
            </span>
          </div>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-50 dark:text-gray-400 dark:hover:bg-white/[0.06] dark:hover:text-white"
            onclick={refresh}
            disabled={loading}
            title="Refresh file list"
          >
            <ArrowPath className="size-3 {loading ? 'animate-spin' : ''}" />
            <span>Refresh</span>
          </button>
        </div>

        {#if documents.length > 0}
          <div class="relative mb-2.5">
            <div class="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-gray-400 dark:text-gray-500">
              <Search className="size-3.5" />
            </div>
            <input
              type="text"
              bind:value={searchQuery}
              placeholder="Filter files…"
              aria-label="Filter files"
              class="h-8 w-full rounded-lg border border-gray-200/80 bg-gray-50/70 pl-8 pr-7 text-xs text-gray-800 outline-hidden transition placeholder:text-gray-400 focus:border-blue-500 focus:bg-white dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-gray-100 dark:placeholder:text-gray-500 dark:focus:border-blue-400"
            />
            {#if searchQuery}
              <button
                type="button"
                onclick={() => (searchQuery = "")}
                aria-label="Clear filter"
                class="absolute inset-y-0 right-2 flex items-center text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              >
                <XMark className="size-3.5" />
              </button>
            {/if}
          </div>
        {/if}

        {#if loading}
          <p class="px-3 py-6 text-center text-xs text-gray-500 dark:text-gray-400" role="status">
            Loading files…
          </p>
        {:else if documents.length === 0}
          <div
            class="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-center dark:border-white/[0.1]"
          >
            <DocumentPage className="mx-auto size-6 text-gray-400 dark:text-gray-500" />
            <p class="mt-2 text-xs font-medium text-gray-700 dark:text-gray-300">
              No files yet
            </p>
            <p class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
              Upload a DOCX, XLSX, PPTX, or PDF, or import from Google Drive to get started.
            </p>
          </div>
        {:else if filteredDocuments.length === 0}
          <p class="px-3 py-6 text-center text-xs text-gray-500 dark:text-gray-400">
            No files match "{searchQuery.trim()}".
          </p>
        {:else}
          <div class="flex max-h-[60vh] flex-col gap-1 overflow-y-auto pr-0.5">
            {#each filteredDocuments as doc (doc.id)}
              <button
                type="button"
                class="group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition {selected?.id ===
                doc.id
                  ? 'bg-gray-100 ring-1 ring-gray-200/90 dark:bg-white/[0.08] dark:ring-white/[0.12]'
                  : 'hover:bg-gray-50 dark:hover:bg-white/[0.04]'}"
                aria-pressed={selected?.id === doc.id}
                onclick={() => selectDocument(doc)}
              >
                <div
                  class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-gray-100/80 dark:bg-white/[0.06]"
                >
                  <MaterialFileIcon filename={doc.filename} className="size-5" />
                </div>
                <div class="min-w-0 flex-1">
                  <span
                    class="block truncate text-xs font-medium text-gray-800 dark:text-gray-100"
                    title={doc.filename}
                  >
                    {doc.filename}
                  </span>
                  <div class="mt-0.5 flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                    <span>Rev {doc.revision}</span>
                    {#if doc.drive_file_id}
                      <span class="text-gray-300 dark:text-gray-600">·</span>
                      <span class="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                        Drive
                      </span>
                    {/if}
                  </div>
                </div>
              </button>
            {/each}
          </div>
        {/if}
      </aside>

      <!-- Detail Pane -->
      <section class="min-w-0">
        {#if selected}
          {@const syncBadge = selected.drive_file_id
            ? statusBadgeMeta(selected.sync?.status || "pending")
            : null}
          <div class="space-y-4">
            <!-- Selected Document Card -->
            <div
              class="rounded-xl border border-gray-200/80 bg-white/70 p-4 dark:border-white/[0.08] dark:bg-white/[0.02]"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="flex min-w-0 flex-1 items-start gap-3">
                  <div
                    class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gray-100/80 dark:bg-white/[0.08]"
                  >
                    <MaterialFileIcon filename={selected.filename} className="size-6" />
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <h2 class="min-w-0 break-words text-sm font-semibold text-gray-900 dark:text-white">
                        {selected.filename}
                      </h2>
                      <span
                        class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-white/[0.08] dark:text-gray-300"
                      >
                        Revision {selected.revision}
                      </span>
                    </div>

                    <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                      {#if selected.drive_file_id && syncBadge}
                        <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {syncBadge.className}">
                          Drive sync: {selected.sync?.status ? syncBadge.label : "Not synced yet"}
                        </span>
                      {:else}
                        <span
                          class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-white/[0.06] dark:text-gray-300"
                        >
                          Local file
                        </span>
                        <span>Changes are saved here. Download a revision to share it.</span>
                      {/if}
                    </div>
                  </div>
                </div>

                <button
                  type="button"
                  class={secondaryButtonClass}
                  onclick={downloadCurrent}
                >
                  <ArrowDownTray className="size-3.5" />
                  <span>Download</span>
                </button>
              </div>

              {#if selected.sync?.error}
                <div
                  class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300"
                >
                  <span class="min-w-0 break-words">{selected.sync.error}</span>
                  {#if selected.sync?.status === "failed" && !selected.sync?.error?.includes("Drive conflict:") && !selected.sync?.error?.includes("no longer has edit access")}
                    <button
                      type="button"
                      class="shrink-0 rounded-md border border-rose-500/30 bg-white/80 px-2.5 py-1 text-xs font-medium text-rose-700 transition hover:bg-white disabled:opacity-50 dark:bg-rose-500/20 dark:text-rose-200 dark:hover:bg-rose-500/30"
                      onclick={retry}
                      disabled={busy}
                    >
                      Retry sync
                    </button>
                  {/if}
                </div>
              {/if}
            </div>

            <!-- Propose an Edit Card -->
            {#if !selected.filename.toLowerCase().endsWith(".pdf")}
              <div
                class="rounded-xl border border-gray-200/80 bg-white/70 p-4 dark:border-white/[0.08] dark:bg-white/[0.02]"
              >
                <div class="flex items-center gap-2">
                  <PencilSquare className="size-4 text-gray-500 dark:text-gray-400" />
                  <h3 class="text-sm font-semibold text-gray-900 dark:text-white">
                    Propose an edit
                  </h3>
                </div>
                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Every edit generates a preview diff first so you can review and approve before saving.
                </p>

                {#if selected.filename.toLowerCase().endsWith(".xlsx")}
                  <div class="mt-3 grid gap-3 sm:grid-cols-3">
                    <label class="min-w-0 text-xs">
                      <span class="mb-1 block font-medium text-gray-600 dark:text-gray-300">
                        Sheet name
                      </span>
                      <input
                        class={inputClass}
                        aria-label="Sheet name"
                        placeholder="Sheet name"
                        bind:value={sheetName}
                      />
                    </label>
                    <label class="min-w-0 text-xs">
                      <span class="mb-1 block font-medium text-gray-600 dark:text-gray-300">
                        Cell (e.g. B2)
                      </span>
                      <input
                        class={inputClass}
                        aria-label="Cell (e.g. B2)"
                        placeholder="Cell (e.g. B2)"
                        bind:value={cellAddress}
                      />
                    </label>
                    <label class="min-w-0 text-xs">
                      <span class="mb-1 block font-medium text-gray-600 dark:text-gray-300">
                        Value or formula
                      </span>
                      <input
                        class={inputClass}
                        aria-label="Value or formula"
                        placeholder="Value or formula"
                        bind:value={cellValue}
                      />
                    </label>
                  </div>
                {:else}
                  <div class="mt-3 grid gap-3 sm:grid-cols-2">
                    <label class="min-w-0 text-xs">
                      <span class="mb-1 block font-medium text-gray-600 dark:text-gray-300">
                        Find
                      </span>
                      <input
                        class={inputClass}
                        aria-label="Find"
                        placeholder="Exact text to find"
                        bind:value={findText}
                      />
                    </label>
                    <label class="min-w-0 text-xs">
                      <span class="mb-1 block font-medium text-gray-600 dark:text-gray-300">
                        Replace with
                      </span>
                      <input
                        class={inputClass}
                        aria-label="Replace with"
                        placeholder="Replacement text"
                        bind:value={replaceText}
                      />
                    </label>
                  </div>
                {/if}

                <div class="mt-3 flex justify-end">
                  <button
                    type="button"
                    class={primaryButtonClass}
                    onclick={propose}
                    disabled={busy ||
                      (selected.filename.toLowerCase().endsWith(".xlsx")
                        ? !sheetName || !cellAddress
                        : !findText)}
                  >
                    Create preview
                  </button>
                </div>
              </div>
            {/if}

            <!-- Previews List -->
            <div>
              <div class="mb-2.5 flex items-center justify-between">
                <h3 class="text-sm font-semibold text-gray-900 dark:text-white">
                  Previews
                </h3>
                {#if previews.length > 0}
                  <span class="text-xs text-gray-500 dark:text-gray-400">
                    {previews.length} {previews.length === 1 ? "preview" : "previews"}
                  </span>
                {/if}
              </div>

              {#if loadingPreviews}
                <div
                  class="rounded-xl border border-gray-200/80 bg-white/50 p-5 text-center text-xs text-gray-500 dark:border-white/[0.08] dark:bg-white/[0.02] dark:text-gray-400"
                  role="status"
                >
                  Loading previews…
                </div>
              {:else if previews.length === 0}
                <div
                  class="rounded-xl border border-dashed border-gray-200 px-5 py-8 text-center dark:border-white/[0.1]"
                >
                  <p class="text-xs font-medium text-gray-700 dark:text-gray-300">
                    No previews yet
                  </p>
                  <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Propose an edit above or ask WeAI in chat to review changes before saving.
                  </p>
                </div>
              {:else}
                <div class="space-y-3">
                  {#each previews as preview (preview.id)}
                    {@const badge = statusBadgeMeta(preview.status)}
                    <div
                      class="rounded-xl border border-gray-200/80 bg-white/70 p-4 dark:border-white/[0.08] dark:bg-white/[0.02]"
                    >
                      <div class="flex flex-wrap items-center justify-between gap-2">
                        <span
                          class="inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium {badge.className}"
                        >
                          {badge.label}
                        </span>
                        <span class="text-[11px] text-gray-500 dark:text-gray-400">
                          Base revision {preview.base_revision}
                        </span>
                      </div>

                      {#if Array.isArray(preview.operations) && preview.operations.length > 0}
                        <ul class="mt-2.5 space-y-1 text-xs text-gray-700 dark:text-gray-200">
                          {#each preview.operations as op}
                            <li class="flex items-start gap-2">
                              <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-gray-400 dark:bg-gray-500"></span>
                              <span class="min-w-0 break-words">{describeOperation(op)}</span>
                            </li>
                          {/each}
                        </ul>
                      {/if}

                      {#if preview.diff}
                        <pre
                          class="mt-3 max-h-64 overflow-auto rounded-lg border border-gray-200/80 bg-gray-50 p-3 font-mono text-[11px] leading-relaxed text-gray-800 dark:border-white/[0.06] dark:bg-black/30 dark:text-gray-200">{preview.diff}</pre>
                      {/if}

                      {#if preview.error}
                        <p class="mt-2.5 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
                          {preview.error}
                        </p>
                      {/if}

                      {#if preview.status === "ready" && preview.base_revision !== selected.revision}
                        <p class="mt-2.5 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                          This preview is based on an older revision. Create a new preview before saving.
                        </p>
                      {/if}

                      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 pt-1">
                        <div class="flex flex-wrap items-center gap-2">
                          {#if preview.status === "ready"}
                            <button
                              type="button"
                              class={primaryButtonClass}
                              onclick={() => commit(preview)}
                              disabled={busy || preview.base_revision !== selected.revision}
                            >
                              <CheckCircle className="size-3.5" />
                              <span>Confirm and commit</span>
                            </button>
                          {/if}
                          {#if preview.status === "ready" || preview.status === "committed"}
                            <button
                              type="button"
                              class={secondaryButtonClass}
                              onclick={() => downloadPreview(preview.id)}
                            >
                              <ArrowDownTray className="size-3.5" />
                              <span>Download preview</span>
                            </button>
                          {/if}
                        </div>

                        {#if Array.isArray(preview.operations) && preview.operations.length > 0}
                          <details class="group text-[11px] text-gray-500 dark:text-gray-400">
                            <summary class="inline-flex cursor-pointer list-none items-center gap-1 hover:text-gray-800 dark:hover:text-gray-200">
                              <span>Technical details</span>
                              <ChevronDown className="size-3 transition-transform group-open:rotate-180" />
                            </summary>
                            <pre
                              class="mt-2 max-h-48 overflow-auto rounded-lg border border-gray-200/80 bg-gray-50 p-2.5 font-mono text-[11px] text-gray-600 dark:border-white/[0.06] dark:bg-black/30 dark:text-gray-300">{JSON.stringify(
                                preview.operations,
                                null,
                                2,
                              )}</pre>
                          </details>
                        {/if}
                      </div>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
        {:else}
          <div
            class="flex min-h-[260px] flex-col items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/40 p-8 text-center dark:border-white/[0.1] dark:bg-white/[0.01]"
          >
            <DocumentPage className="size-8 text-gray-400 dark:text-gray-500" />
            <p class="mt-3 text-sm font-medium text-gray-700 dark:text-gray-200">
              Select a document
            </p>
            <p class="mt-1 max-w-xs text-xs text-gray-500 dark:text-gray-400">
              Choose a file from the left to download revisions, propose edits, or review pending changes.
            </p>
          </div>
        {/if}
      </section>
    </div>
  {/if}
</div>
