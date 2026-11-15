import { describe, expect, it } from "vitest";
import {
  APPROVAL_WINDOW_MS,
  buildAuthenticatedCommitRequest,
  buildAuthenticatedRejectRequest,
  computeFixedExpiryMs,
  computeRemainingApprovalMs,
  formatApprovalCountdown,
  inferDocumentFormat,
  isPreviewApprovalExpired,
  parseFormatDiffBlocks,
  resolveCoverageStatus,
  type PreviewItem,
} from "./DocumentChangeReview.svelte";

const makePreview = (overrides: Partial<PreviewItem> = {}): PreviewItem => ({
  id: "prev-001",
  preview_id: "prev-001",
  document_id: "doc-001",
  filename: "financial_model.xlsx",
  format_type: "xlsx",
  status: "ready",
  diff: "--- Sheet1!B2\n- 100\n+ 250",
  diff_summary: "--- Sheet1!B2\n- 100\n+ 250",
  error: null,
  base_revision: 3,
  base_sha256: "a".repeat(64),
  artifact_sha256: "b".repeat(64),
  patch_hash: "c".repeat(64),
  canonical_patch_hash: "c".repeat(64),
  created_at: "2026-11-14T15:00:00.000Z",
  expires_at: "2026-11-14T15:15:00.000Z",
  coverage_status: "complete",
  ...overrides,
});

describe("DocumentChangeReview V2 Revision Review & 15-Minute Approval Protocol", () => {
  it("infers document format from explicit format_type or filename extension", () => {
    expect(inferDocumentFormat("budget.xlsx", "xlsx")).toBe("xlsx");
    expect(inferDocumentFormat("contract.DOCX", null)).toBe("docx");
    expect(inferDocumentFormat("deck.pptx", undefined)).toBe("pptx");
    expect(inferDocumentFormat("policy.pdf", "PDF")).toBe("pdf");
    expect(inferDocumentFormat("notes.txt", null)).toBe("unknown");
  });

  it("enforces strict 15-minute approval window anchored to created_at and never extends on retry", () => {
    const createdMs = Date.parse("2026-11-14T15:00:00.000Z");
    const validExpiresIso = "2026-11-14T15:15:00.000Z";
    const extendedRetryIso = "2026-11-14T15:30:00.000Z";

    expect(APPROVAL_WINDOW_MS).toBe(15 * 60 * 1000);
    expect(computeFixedExpiryMs("2026-11-14T15:00:00.000Z", validExpiresIso)).toBe(
      createdMs + APPROVAL_WINDOW_MS,
    );
    // Even if a retry response attempted to push expires_at to +30m, computeFixedExpiryMs clamps to created_at + 15m
    expect(
      computeFixedExpiryMs("2026-11-14T15:00:00.000Z", extendedRetryIso),
    ).toBe(createdMs + APPROVAL_WINDOW_MS);
  });

  it("computes live countdown and locks expired state at exactly 15 minutes", () => {
    const preview = makePreview();
    const startMs = Date.parse("2026-11-14T15:00:00.000Z");
    const midMs = Date.parse("2026-11-14T15:05:30.000Z");
    const exactExpiryMs = Date.parse("2026-11-14T15:15:00.000Z");

    expect(isPreviewApprovalExpired(preview, startMs)).toBe(false);
    expect(formatApprovalCountdown(computeRemainingApprovalMs(preview, startMs))).toBe(
      "15:00",
    );

    expect(isPreviewApprovalExpired(preview, midMs)).toBe(false);
    expect(formatApprovalCountdown(computeRemainingApprovalMs(preview, midMs))).toBe(
      "09:30",
    );

    expect(isPreviewApprovalExpired(preview, exactExpiryMs)).toBe(true);
    expect(computeRemainingApprovalMs(preview, exactExpiryMs)).toBe(0);
    expect(
      formatApprovalCountdown(computeRemainingApprovalMs(preview, exactExpiryMs)),
    ).toBe("00:00 (Expired)");
  });

  it("parses format-specific diff blocks for XLSX, DOCX, and PPTX", () => {
    const xlsxPreview = makePreview({
      filename: "model.xlsx",
      format_type: "xlsx",
      structured_diffs: [
        {
          format: "xlsx",
          op: "xlsx.set_cell_value",
          sheet: "Revenue",
          coordinate: "C4",
          before: 1200,
          after: 1450,
        },
      ],
    });
    const xlsxBlocks = parseFormatDiffBlocks(xlsxPreview);
    expect(xlsxBlocks).toHaveLength(1);
    expect(xlsxBlocks[0]).toMatchObject({
      format: "xlsx",
      locator: "Revenue!C4",
      sheet: "Revenue",
      coordinate: "C4",
      before: "1200",
      after: "1450",
      op: "xlsx.set_cell_value",
    });

    const docxPreview = makePreview({
      filename: "agreement.docx",
      format_type: "docx",
      structured_diffs: undefined,
      diff_summary: "--- paragraph:4 (occurrence 1)\n- Draft Clause\n+ Final Approved Clause",
    });
    const docxBlocks = parseFormatDiffBlocks(docxPreview);
    expect(docxBlocks).toHaveLength(1);
    expect(docxBlocks[0]).toMatchObject({
      format: "docx",
      paragraph_order: 4,
      before: "Draft Clause",
      after: "Final Approved Clause",
    });

    const pptxPreview = makePreview({
      filename: "pitch.pptx",
      format_type: "pptx",
      structured_diffs: undefined,
      diff_summary: "--- slide:2/shape:7\n- Q3 Forecast\n+ Q4 Actuals",
    });
    const pptxBlocks = parseFormatDiffBlocks(pptxPreview);
    expect(pptxBlocks).toHaveLength(1);
    expect(pptxBlocks[0]).toMatchObject({
      format: "pptx",
      slide_order: 2,
      shape_id: 7,
      before: "Q3 Forecast",
      after: "Q4 Actuals",
    });
  });

  it("resolves coverage and ingestion status badges accurately", () => {
    expect(resolveCoverageStatus(makePreview({ coverage_status: "complete" }))).toEqual({
      label: "100% Indexed & Verified",
      state: "complete",
    });
    expect(
      resolveCoverageStatus(
        makePreview({ coverage_status: null, ingestion_status: "indexing" }),
      ),
    ).toEqual({
      label: "Indexing in progress",
      state: "indexing",
    });
    expect(
      resolveCoverageStatus(
        makePreview({ coverage_status: "failed", status: "failed" }),
      ),
    ).toEqual({
      label: "Verification failed",
      state: "failed",
    });
  });

  it("builds authenticated Approve & Commit request with exact patch_hash, base_revision, and idempotency_key", () => {
    const preview = makePreview();
    const validNow = Date.parse("2026-11-14T15:04:00.000Z");

    const req = buildAuthenticatedCommitRequest(
      preview,
      "jwt-token-user-123",
      "idem-commit-001",
      validNow,
    );

    expect(req.url).toBe("/previews/prev-001/commit");
    expect(req.init.method).toBe("POST");
    expect(req.init.headers).toMatchObject({
      Authorization: "Bearer jwt-token-user-123",
      "Idempotency-Key": "idem-commit-001",
      "Content-Type": "application/json",
    });
    expect(req.payload).toEqual({
      base_revision: 3,
      patch_hash: "c".repeat(64),
      idempotency_key: "idem-commit-001",
      base_sha256: "a".repeat(64),
      artifact_sha256: "b".repeat(64),
    });
  });

  it("blocks commit request when approval is expired or unauthenticated, and builds authenticated reject request", () => {
    const preview = makePreview();
    const expiredNow = Date.parse("2026-11-14T15:15:01.000Z");

    expect(() =>
      buildAuthenticatedCommitRequest(preview, "jwt-token", "idem-1", expiredNow),
    ).toThrow(/expired/i);

    expect(() =>
      buildAuthenticatedCommitRequest(
        preview,
        "",
        "idem-1",
        Date.parse("2026-11-14T15:02:00.000Z"),
      ),
    ).toThrow(/Authentication token/i);

    const rejectReq = buildAuthenticatedRejectRequest(
      "prev-001",
      "jwt-token-user-123",
      "Formula output needs review",
    );
    expect(rejectReq.url).toBe("/previews/prev-001/reject");
    expect(rejectReq.init.method).toBe("POST");
    expect(rejectReq.init.headers).toMatchObject({
      Authorization: "Bearer jwt-token-user-123",
    });
  });
});
