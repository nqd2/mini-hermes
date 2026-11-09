import { describe, expect, it } from "vitest";
import {
  getChatSubmissionDispatchDecision,
  hasPendingChatFileTransfers,
  type ChatSubmissionDispatchOptions,
} from "./chatSubmissionDispatch";

describe("chat submission dispatch", () => {
  const createOptions = (
    overrides: Partial<ChatSubmissionDispatchOptions> = {},
  ) => ({
    isBridgeRequest: false,
    hasPendingFileTransfers: false,
    assistantGenerating: false,
    messageQueueEnabled: true,
    ...overrides,
  });

  it("detects queued file transfers that are uploading or failed", () => {
    expect(
      hasPendingChatFileTransfers(
        [
          { files: [{ status: "uploaded" }, { status: "uploading" }] },
          { files: [{ status: "error" }] },
        ],
        [],
      ),
    ).toBe(true);
  });

  it("ignores pending, uploaded, and empty file lists", () => {
    expect(
      hasPendingChatFileTransfers(
        [
          { files: [{ status: "pending" }, { status: "uploaded" }] },
          { files: [] },
        ],
        [],
      ),
    ).toBe(false);
  });

  it("detects current-turn files that are still uploading or failed", () => {
    expect(hasPendingChatFileTransfers([], [{ status: "error" }])).toBe(true);
  });

  it("defers bridge submissions before queueing when a file transfer is pending", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({
          isBridgeRequest: true,
          hasPendingFileTransfers: true,
          assistantGenerating: true,
        }),
      ),
    ).toEqual({ action: "defer", reason: "file-transfer" });
  });

  it("queues ordinary submissions behind file transfers without snapshotting attachments", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({ hasPendingFileTransfers: true }),
      ),
    ).toEqual({
      action: "queue",
      reason: "file-transfer",
      snapshotFiles: false,
    });
  });

  it("defers a bridge submission while its assistant response is still generating", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({ isBridgeRequest: true, assistantGenerating: true }),
      ),
    ).toEqual({ action: "defer", reason: "assistant-generating" });
  });

  it("queues a snapshot of ordinary submissions while an assistant response is generating", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({ assistantGenerating: true }),
      ),
    ).toEqual({
      action: "queue",
      reason: "assistant-generating",
      snapshotFiles: true,
    });
  });

  it("interrupts the running assistant only when message queueing is disabled", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({
          assistantGenerating: true,
          messageQueueEnabled: false,
        }),
      ),
    ).toEqual({ action: "interrupt" });
  });

  it("sends immediately when there are no uploads or active assistant response", () => {
    expect(getChatSubmissionDispatchDecision(createOptions())).toEqual({
      action: "send",
    });
  });

  it("does not interrupt an idle chat when message queueing is disabled", () => {
    expect(
      getChatSubmissionDispatchDecision(
        createOptions({ messageQueueEnabled: false }),
      ),
    ).toEqual({ action: "send" });
  });
});
