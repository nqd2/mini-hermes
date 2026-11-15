<script context="module" lang="ts">
  export const APPROVAL_WINDOW_MS = 15 * 60 * 1000;

  export type SupportedDocFormat = "xlsx" | "docx" | "pptx" | "pdf" | "unknown";

  export type StructuredDiffEntry = {
    format: SupportedDocFormat;
    locator: string;
    sheet?: string;
    coordinate?: string;
    paragraph_order?: number;
    occurrence?: number;
    slide_order?: number;
    shape_id?: number | string;
    before: string;
    after: string;
    op?: string;
  };

  export type PreviewItem = {
    id: string;
    preview_id?: string;
    document_id: string;
    filename: string;
    format_type?: string | null;
    status: string;
    diff: string | null;
    diff_summary?: string | null;
    structured_diffs?: Array<Record<string, unknown>>;
    error: string | null;
    base_revision: number;
    base_sha256: string | null;
    artifact_sha256: string | null;
    patch_hash: string | null;
    canonical_patch_hash?: string | null;
    created_at?: string | null;
    expires_at?: string | null;
    coverage_status?: string | null;
    ingestion_status?: string | null;
    revision?: number;
    viewer_url?: string;
    download_url?: string;
    sync_status?: string;
  };

  export function inferDocumentFormat(
    filename: string,
    formatType?: string | null,
  ): SupportedDocFormat {
    const explicit = (formatType || "").trim().toLowerCase();
    if (
      explicit === "xlsx" ||
      explicit === "docx" ||
      explicit === "pptx" ||
      explicit === "pdf"
    ) {
      return explicit;
    }
    const lower = (filename || "").trim().toLowerCase();
    if (lower.endsWith(".xlsx")) return "xlsx";
    if (lower.endsWith(".docx")) return "docx";
    if (lower.endsWith(".pptx")) return "pptx";
    if (lower.endsWith(".pdf")) return "pdf";
    return "unknown";
  }

  export function computeFixedExpiryMs(
    createdAt?: string | number | null,
    expiresAt?: string | number | null,
  ): number | null {
    const createdMs =
      typeof createdAt === "number"
        ? createdAt
        : createdAt
          ? Date.parse(createdAt)
          : NaN;
    const expiresMs =
      typeof expiresAt === "number"
        ? expiresAt
        : expiresAt
          ? Date.parse(expiresAt)
          : NaN;

    const fixedFromCreated = Number.isFinite(createdMs)
      ? createdMs + APPROVAL_WINDOW_MS
      : NaN;

    if (Number.isFinite(fixedFromCreated) && Number.isFinite(expiresMs)) {
      // Never allow retries or mutated expires_at to extend beyond created_at + 15 minutes
      return Math.min(fixedFromCreated, expiresMs);
    }
    if (Number.isFinite(fixedFromCreated)) return fixedFromCreated;
    if (Number.isFinite(expiresMs)) return expiresMs;
    return null;
  }

  export function computeRemainingApprovalMs(
    preview: Pick<PreviewItem, "created_at" | "expires_at" | "status"> | null,
    nowMs: number = Date.now(),
  ): number {
    if (!preview) return 0;
    if (preview.status === "expired" || preview.status === "rejected") return 0;
    const deadline = computeFixedExpiryMs(
      preview.created_at,
      preview.expires_at,
    );
    if (deadline === null) return APPROVAL_WINDOW_MS;
    return Math.max(0, deadline - nowMs);
  }

  export function isPreviewApprovalExpired(
    preview: Pick<PreviewItem, "created_at" | "expires_at" | "status"> | null,
    nowMs: number = Date.now(),
  ): boolean {
    if (!preview) return true;
    if (preview.status === "expired") return true;
    if (preview.status === "committed") return false;
    const deadline = computeFixedExpiryMs(
      preview.created_at,
      preview.expires_at,
    );
    if (deadline === null) return false;
    return nowMs >= deadline;
  }

  export function formatApprovalCountdown(remainingMs: number): string {
    if (!Number.isFinite(remainingMs) || remainingMs <= 0) {
      return "00:00 (Expired)";
    }
    const totalSeconds = Math.floor(remainingMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const mm = String(minutes).padStart(2, "0");
    const ss = String(seconds).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  export function parseFormatDiffBlocks(
    preview:
      | Pick<
          PreviewItem,
          | "filename"
          | "format_type"
          | "diff"
          | "diff_summary"
          | "structured_diffs"
        >
      | null,
  ): StructuredDiffEntry[] {
    if (!preview) return [];
    const format = inferDocumentFormat(preview.filename, preview.format_type);

    if (
      Array.isArray(preview.structured_diffs) &&
      preview.structured_diffs.length > 0
    ) {
      return preview.structured_diffs.map((raw) => {
        const entryFmt = inferDocumentFormat(
          preview.filename,
          (raw.format as string) || preview.format_type,
        );
        const sheet = raw.sheet ? String(raw.sheet) : undefined;
        const coordinate = raw.coordinate ? String(raw.coordinate) : undefined;
        const paragraphOrder =
          typeof raw.paragraph_order === "number"
            ? raw.paragraph_order
            : undefined;
        const slideOrder =
          typeof raw.slide_order === "number" ? raw.slide_order : undefined;
        const shapeId =
          raw.shape_id !== undefined && raw.shape_id !== null
            ? (raw.shape_id as number | string)
            : undefined;

        let locator = String(raw.locator || "");
        if (!locator) {
          if (entryFmt === "xlsx" && sheet && coordinate) {
            locator = `${sheet}!${coordinate}`;
          } else if (entryFmt === "docx" && paragraphOrder !== undefined) {
            locator = `Paragraph #${paragraphOrder}`;
          } else if (
            entryFmt === "pptx" &&
            slideOrder !== undefined &&
            shapeId !== undefined
          ) {
            locator = `Slide ${slideOrder} · Shape ${shapeId}`;
          } else {
            locator = entryFmt.toUpperCase();
          }
        }

        return {
          format: entryFmt,
          locator,
          sheet,
          coordinate,
          paragraph_order: paragraphOrder,
          occurrence:
            typeof raw.occurrence === "number" ? raw.occurrence : undefined,
          slide_order: slideOrder,
          shape_id: shapeId,
          before: raw.before === null || raw.before === undefined ? "∅" : String(raw.before),
          after: raw.after === null || raw.after === undefined ? "∅" : String(raw.after),
          op: raw.op ? String(raw.op) : undefined,
        };
      });
    }

    const rawText = (preview.diff_summary || preview.diff || "").trim();
    if (!rawText) return [];

    const lines = rawText.split(/\r?\n/);
    const entries: StructuredDiffEntry[] = [];
    let currentHeader = "";
    let beforeLines: string[] = [];
    let afterLines: string[] = [];

    const flushBlock = () => {
      if (!currentHeader && beforeLines.length === 0 && afterLines.length === 0)
        return;
      const loc = currentHeader.replace(/^---\s*/, "").trim() || format.toUpperCase();
      const entry: StructuredDiffEntry = {
        format,
        locator: loc,
        before: beforeLines.join("\n") || "∅",
        after: afterLines.join("\n") || "∅",
      };
      if (format === "xlsx" && loc.includes("!")) {
        const [sheet, coordinate] = loc.split("!", 2);
        entry.sheet = sheet;
        entry.coordinate = coordinate;
      } else if (format === "docx") {
        const m = loc.match(/paragraph:(\d+)/i);
        if (m) entry.paragraph_order = Number(m[1]);
      } else if (format === "pptx") {
        const m = loc.match(/slide:(\d+)\/shape:(\S+)/i);
        if (m) {
          entry.slide_order = Number(m[1]);
          entry.shape_id = Number.isNaN(Number(m[2])) ? m[2] : Number(m[2]);
        }
      }
      entries.push(entry);
      beforeLines = [];
      afterLines = [];
    };

    for (const line of lines) {
      if (line.startsWith("--- ")) {
        flushBlock();
        currentHeader = line;
      } else if (line.startsWith("- ")) {
        beforeLines.push(line.slice(2));
      } else if (line.startsWith("+ ")) {
        afterLines.push(line.slice(2));
      }
    }
    flushBlock();
    return entries;
  }

  export function resolveCoverageStatus(
    preview:
      | Pick<
          PreviewItem,
          "coverage_status" | "ingestion_status" | "sync_status" | "status"
        >
      | null,
  ): { label: string; state: "complete" | "indexing" | "failed" | "ready" } {
    if (!preview) {
      return { label: "Unavailable", state: "failed" };
    }
    const raw = (
      preview.coverage_status ||
      preview.ingestion_status ||
      preview.sync_status ||
      ""
    )
      .trim()
      .toLowerCase();

    if (raw === "complete" || raw === "verified" || raw === "available") {
      return { label: "100% Indexed & Verified", state: "complete" };
    }
    if (raw === "indexing" || raw === "processing" || raw === "pending") {
      return { label: "Indexing in progress", state: "indexing" };
    }
    if (raw === "failed" || raw === "error" || preview.status === "failed") {
      return { label: "Verification failed", state: "failed" };
    }
    return { label: "Staged & Verified", state: "ready" };
  }

  export function buildAuthenticatedCommitRequest(
    preview: PreviewItem,
    token: string,
    idempotencyKey?: string,
    nowMs: number = Date.now(),
  ): { url: string; init: RequestInit; payload: Record<string, unknown> } {
    const cleanToken = (token || "").trim();
    if (!cleanToken) {
      throw new Error("Authentication token is required to approve and commit.");
    }
    if (isPreviewApprovalExpired(preview, nowMs)) {
      throw new Error("Approval window (15 minutes) has expired. Create a new preview to commit.");
    }
    if (preview.status !== "ready") {
      throw new Error(`Preview in status '${preview.status}' cannot be committed.`);
    }
    const patchHash = (
      preview.patch_hash ||
      preview.canonical_patch_hash ||
      ""
    ).trim();
    if (!patchHash) {
      throw new Error("Canonical patch_hash is required to commit preview.");
    }

    const previewId = preview.preview_id || preview.id;
    const effectiveIdemKey =
      (idempotencyKey || "").trim() || `commit:${previewId}:${patchHash.slice(0, 16)}`;

    const payload: Record<string, unknown> = {
      base_revision: preview.base_revision,
      patch_hash: patchHash,
      idempotency_key: effectiveIdemKey,
      ...(preview.base_sha256 ? { base_sha256: preview.base_sha256 } : {}),
      ...(preview.artifact_sha256
        ? { artifact_sha256: preview.artifact_sha256 }
        : {}),
    };

    return {
      url: `/previews/${encodeURIComponent(previewId)}/commit`,
      init: {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${cleanToken}`,
          "Idempotency-Key": effectiveIdemKey,
        },
        body: JSON.stringify(payload),
      },
      payload,
    };
  }

  export function buildAuthenticatedRejectRequest(
    previewId: string,
    token: string,
    reason: string = "User rejected proposed document changes",
  ): { url: string; init: RequestInit } {
    const cleanToken = (token || "").trim();
    if (!cleanToken) {
      throw new Error("Authentication token is required to reject preview.");
    }
    return {
      url: `/previews/${encodeURIComponent(previewId)}/reject`,
      init: {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${cleanToken}`,
        },
        body: JSON.stringify({ reason }),
      },
    };
  }
</script>

<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { WEBUI_API_BASE_URL } from "$lib/constants";
  import OfficeDocumentPreview from "$lib/components/common/OfficeDocumentPreview.svelte";
  import DocumentViewerShell from "$lib/components/common/DocumentViewerShell.svelte";
  import XMark from "$lib/components/icons/XMark.svelte";

  export let chatId = "";

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
  let viewerZoom: number | "fit" = "fit";
  let previewLoadId = "";
  let panelOpen = false;
  let loading = false;
  let committing = false;
  let rejecting = false;
  let downloading = false;
  let error = "";
  let commitMessage = "";
  let dismissedPreviewId = "";
  let latestPreviewSignature = "";
  let latestArtifactSignature = "";
  let timer: ReturnType<typeof setInterval>;
  let countdownTimer: ReturnType<typeof setInterval>;
  let nowTimestamp = Date.now();
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

  $: activeFormat = active
    ? inferDocumentFormat(active.filename, active.format_type)
    : "unknown";
  $: remainingApprovalMs = computeRemainingApprovalMs(active, nowTimestamp);
  $: approvalCountdownLabel = formatApprovalCountdown(remainingApprovalMs);
  $: isApprovalExpired = isPreviewApprovalExpired(active, nowTimestamp);
  $: structuredDiffs = parseFormatDiffBlocks(active);
  $: coverageBadge = resolveCoverageStatus(active);

  const normalizePreviewItem = (raw: Record<string, any>): PreviewItem => {
    const id = String(raw.id || raw.preview_id || "");
    const patchHash =
      raw.patch_hash || raw.canonical_patch_hash || null;
    const diffText =
      raw.diff !== undefined && raw.diff !== null
        ? String(raw.diff)
        : raw.diff_summary !== undefined && raw.diff_summary !== null
          ? String(raw.diff_summary)
          : null;
    return {
      id,
      preview_id: String(raw.preview_id || id),
      document_id: String(raw.document_id || ""),
      filename: String(raw.filename || raw.document_name || "document"),
      format_type: raw.format_type || null,
      status: String(raw.status || "ready"),
      diff: diffText,
      diff_summary: raw.diff_summary || diffText,
      structured_diffs: Array.isArray(raw.structured_diffs)
        ? raw.structured_diffs
        : undefined,
      error: raw.error || null,
      base_revision: Number(raw.base_revision ?? raw.base_revision_no ?? 1),
      base_sha256: raw.base_sha256 || null,
      artifact_sha256: raw.artifact_sha256 || null,
      patch_hash: patchHash,
      canonical_patch_hash: patchHash,
      created_at: raw.created_at || null,
      expires_at: raw.expires_at || null,
      coverage_status: raw.coverage_status || null,
      ingestion_status: raw.ingestion_status || null,
      revision: raw.revision,
      viewer_url: raw.viewer_url,
      download_url: raw.download_url,
      sync_status: raw.sync_status,
    };
  };

  const api = async (path: string, init: RequestInit = {}) => {
    const token =
      typeof localStorage !== "undefined" ? localStorage.token || "" : "";
    const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        ...init.headers,
      },
    });
    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}));
      const detail = errBody?.detail;
      const msg =
        typeof detail === "object" && detail !== null
          ? detail.message || detail.code || response.statusText
          : detail || response.statusText;
      throw new Error(msg);
    }
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
      const rawPreviews = await response.json();
      previews = (Array.isArray(rawPreviews) ? rawPreviews : []).map(
        normalizePreviewItem,
      );
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
                (preview.revision || preview.base_revision + 1) ===
                  artifact.revision,
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
    error = "";
    try {
      const response = await api(
        `/documents/${active.document_id}/content?revision=${number}`,
      );
      historyData = await response.arrayBuffer();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    }
  };

  const commit = async () => {
    if (!active || active.status !== "ready" || committing || isApprovalExpired)
      return;
    committing = true;
    error = "";
    try {
      const rawApproval =
        active.base_sha256 &&
        active.artifact_sha256 &&
        (active.patch_hash || active.canonical_patch_hash)
          ? active
          : normalizePreviewItem(await (await api(`/previews/${active.id}`)).json());

      const token =
        typeof localStorage !== "undefined" ? localStorage.token || "" : "";
      const commitReq = buildAuthenticatedCommitRequest(
        rawApproval,
        token,
        undefined,
         Date.now(),
      );
      const response = await api(commitReq.url, commitReq.init);
      const result = await response.json();
      commitMessage = "Saved as a new local revision.";
      active = {
        ...active,
        status: "committed",
        revision: result.revision ?? result.revision_no,
        sync_status: result.sync_status || result.commit_status,
      };
      previews = [active, ...previews.filter((item) => item.id !== active?.id)];
      panelOpen = true;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      committing = false;
    }
  };

  const reject = async () => {
    if (!active || active.status !== "ready" || rejecting) return;
    rejecting = true;
    error = "";
    try {
      const token =
        typeof localStorage !== "undefined" ? localStorage.token || "" : "";
      const rejectReq = buildAuthenticatedRejectRequest(active.id, token);
      await api(rejectReq.url, rejectReq.init);
      active = { ...active, status: "rejected" };
      previews = previews.map((p) => (p.id === active?.id ? active : p));
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      rejecting = false;
    }
  };

  const retry = async () => {
    if (!active || active.status !== "failed") return;
    error = "";
    try {
      const response = await api(`/previews/${active.id}/retry`, {
        method: "POST",
      });
      const updated = normalizePreviewItem(await response.json());
      // Ensure original created_at and expires_at are preserved on retry
      active = {
        ...active,
        ...updated,
        created_at: active.created_at || updated.created_at,
        expires_at: active.expires_at || updated.expires_at,
        error: null,
      };
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
    countdownTimer = setInterval(() => {
      nowTimestamp = Date.now();
    }, 1000);
  });

  onDestroy(() => {
    clearInterval(timer);
    clearInterval(countdownTimer);
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
    documentId={active.status === "committed" ? active.document_id : null}
    revision={selectedHistoryRevision ||
      active.revision ||
      active.base_revision + 1}
    size={(selectedHistoryRevision && historyData
      ? historyData.byteLength
      : viewMode === "base" && baseData
        ? baseData.byteLength
        : previewData?.byteLength) ?? null}
    accessLabel="Can edit"
    onClose={closePanel}
    onDownload={download}
    onRevisionSelect={selectHistoryRevision}
    onShowText={active.diff || structuredDiffs.length > 0
      ? () => (viewMode = viewMode === "diff" ? "proposed" : "diff")
      : null}
    textMode={viewMode === "diff"}
    onShowOriginal={active.status === "ready" && active.base_revision > 0
      ? () => {
          if (viewMode === "base") viewMode = "proposed";
          else void showBaseRevision();
        }
      : null}
    originalMode={viewMode === "base"}
    pageCountOverride={viewerPageCount}
    currentPageOverride={viewerCurrentPage}
    onPageSelect={(page) => (viewerTargetPage = page)}
    otherFiles={[...previews, ...visibleArtifacts].map((item) => ({
      id: item.id,
      filename: item.filename,
    }))}
    onFileSelect={(id) => {
      const item = [...previews, ...visibleArtifacts].find(
        (entry) => entry.id === id,
      );
      if (item) selectPreview(item);
    }}
  >
    <!-- Status & 15-Minute Approval Countdown Banner -->
    <div
      class="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-[#161616] px-4 py-2 text-xs text-gray-300"
      data-testid="review-status-banner"
    >
      <div class="flex items-center gap-2">
        <span
          class="rounded bg-white/10 px-2 py-0.5 font-mono uppercase text-white"
          data-testid="format-badge"
        >
          {activeFormat}
        </span>
        <span
          class="rounded px-2 py-0.5 {coverageBadge.state === 'complete'
            ? 'bg-emerald-950 text-emerald-300'
            : coverageBadge.state === 'failed'
              ? 'bg-red-950 text-red-300'
              : 'bg-blue-950 text-blue-300'}"
          data-testid="coverage-badge"
        >
          {coverageBadge.label}
        </span>
        {#if active.patch_hash}
          <span
            class="hidden font-mono text-[11px] text-gray-400 sm:inline"
            title={active.patch_hash}
          >
            patch:{active.patch_hash.slice(0, 12)}…
          </span>
        {/if}
      </div>

      {#if active.status === "ready"}
        <div
          class="flex items-center gap-2 font-mono {isApprovalExpired
            ? 'text-red-400'
            : 'text-amber-300'}"
          data-testid="approval-countdown"
        >
          <span>
            {isApprovalExpired
              ? "Approval Window Expired (15m limit)"
              : `Approval expires in ${approvalCountdownLabel}`}
          </span>
        </div>
      {/if}
    </div>

    <div class="min-h-full">
      {#if active.status === "pending" || active.status === "processing"}
        <p class="p-4 text-sm text-gray-500">Đang tạo preview…</p>
      {:else if active.status === "failed"}
        <div class="space-y-3 p-4">
          <p class="text-sm text-red-500">
            {active.error || "Không tạo được preview."}
          </p>
          <button
            class="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700"
            on:click={retry}
          >
            Thử chuẩn hóa operation và tạo preview lại
          </button>
        </div>
      {:else if active.status === "rejected"}
        <div class="p-4 text-sm text-amber-400" role="status">
          Bản đề xuất chỉnh sửa đã bị từ chối. Tài liệu gốc giữ nguyên ở revision {active.base_revision}.
        </div>
      {:else if active.status === "ready" || active.status === "committed"}
        {#if active.status === "committed"}
          <p class="sr-only" role="status">
            {commitMessage || "Saved in your Personal Folder."}
          </p>
        {/if}
        {#if loading}
          <p class="p-4 text-sm text-gray-500">Đang tải nội dung tài liệu…</p>
        {:else if selectedHistoryRevision && historyData}
          <OfficeDocumentPreview
            filename={active.filename}
            data={historyData}
            bind:pageCount={viewerPageCount}
            bind:currentPage={viewerCurrentPage}
            targetPage={viewerTargetPage}
            zoom={viewerZoom}
          />
        {:else if viewMode === "diff"}
          <!-- Format-Specific Structured Diff View (XLSX, DOCX, PPTX) -->
          <div class="space-y-3 p-4" data-testid="structured-diff-view">
            {#if structuredDiffs.length > 0}
              {#each structuredDiffs as entry, idx (idx)}
                <div
                  class="rounded-lg border border-white/10 bg-[#181818] p-3 text-xs"
                >
                  <div
                    class="mb-2 flex items-center justify-between font-mono text-gray-300"
                  >
                    <span class="font-semibold text-white">{entry.locator}</span>
                    {#if entry.op}
                      <span class="rounded bg-white/5 px-1.5 py-0.5 text-[10px]">
                        {entry.op}
                      </span>
                    {/if}
                  </div>
                  <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
                    <div
                      class="rounded border border-red-900/40 bg-red-950/30 p-2 font-mono text-red-200"
                    >
                      <div class="mb-1 text-[10px] uppercase text-red-400">
                        Before (Rev {active.base_revision})
                      </div>
                      <div class="whitespace-pre-wrap break-words">
                        {entry.before}
                      </div>
                    </div>
                    <div
                      class="rounded border border-emerald-900/40 bg-emerald-950/30 p-2 font-mono text-emerald-200"
                    >
                      <div class="mb-1 text-[10px] uppercase text-emerald-400">
                        Proposed (Rev {active.base_revision + 1})
                      </div>
                      <div class="whitespace-pre-wrap break-words">
                        {entry.after}
                      </div>
                    </div>
                  </div>
                </div>
              {/each}
            {:else}
              <pre class="whitespace-pre-wrap break-words p-2 text-xs">{active.diff ||
                  "Không có diff dạng văn bản."}</pre>
            {/if}
          </div>
        {:else if viewMode === "base"}
          <OfficeDocumentPreview
            filename={active.filename}
            data={baseData}
            bind:pageCount={viewerPageCount}
            bind:currentPage={viewerCurrentPage}
            targetPage={viewerTargetPage}
            zoom={viewerZoom}
          />
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
      {#if error}
        <p class="p-4 text-sm text-red-500" role="alert">
          {error}
        </p>
      {/if}
    </div>

    <footer
      slot="action"
      class="flex shrink-0 items-center justify-between gap-2 border-t border-white/10 bg-[#101010] p-3 {active.status ===
        'ready' || active.status === 'expired'
        ? ''
        : 'hidden'}"
    >
      <div class="text-xs text-gray-400">
        {#if isApprovalExpired}
          <span class="text-red-400" data-testid="expired-lock-notice">
            Phê duyệt đã hết hạn sau 15 phút. Vui lòng tạo bản xem trước mới.
          </span>
        {:else}
          <span>Base rev #{active.base_revision}</span>
        {/if}
      </div>
      <div class="flex items-center gap-2">
        {#if active.status === "ready"}
          <button
            class="rounded-lg border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-50"
            disabled={committing || rejecting}
            on:click={reject}
            data-testid="reject-preview-btn"
          >
            {rejecting ? "Đang hủy…" : "Từ chối"}
          </button>
          <button
            class="rounded-lg bg-black px-4 py-2 text-sm text-white dark:bg-white dark:text-black disabled:cursor-not-allowed disabled:opacity-50"
            disabled={committing || rejecting || isApprovalExpired}
            on:click={commit}
            data-testid="approve-commit-btn"
          >
            {#if isApprovalExpired}
              Đã hết hạn phê duyệt (15m)
            {:else if committing}
              Đang lưu…
            {:else}
              Chấp nhận và lưu
            {/if}
          </button>
        {/if}
      </div>
    </footer>
  </DocumentViewerShell>
{/if}
