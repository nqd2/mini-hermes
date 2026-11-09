import { describe, expect, it, vi } from "vitest";
import {
  executeChatSubmission,
  type ChatSubmissionExecutorOptions,
} from "./chatSubmissionExecutor";

type History = { id: string };
type File = { id: string; status: string };
type Options = ChatSubmissionExecutorOptions<History, File>;

describe("executeChatSubmission", () => {
  const createHarness = (overrides: Partial<Options> = {}) => {
    const history = { id: "history-1" };
    let currentFiles: File[] = [{ id: "file-1", status: "uploaded" }];
    let queuedMessages: { files?: File[] | null }[] = [];
    const events: string[] = [];
    const bridgeRequest = { userMessageId: "user-1", modelId: "model-1" };
    const isCurrentHistory = vi.fn(() => true);
    const resolveChatId = vi.fn(async () => "chat-1");
    const ensureChatId = vi.fn(async () => "chat-1");
    const hasPendingFiles = vi.fn(() => false);
    const uploadPendingFiles = vi.fn(async () => true);
    const setProcessing = vi.fn((state: "uploading" | "") => {
      events.push(`processing:${state}`);
    });
    const notifyPreparationError = vi.fn(() =>
      events.push("preparation-error"),
    );
    const getQueuedMessages = vi.fn(() => queuedMessages);
    const getCurrentFiles = vi.fn(() => currentFiles);
    const isAssistantGenerating = vi.fn(() => false);
    const isMessageQueueEnabled = vi.fn(() => true);
    const createRequestId = vi.fn(() => "queued-1");
    const enqueueRequest = vi.fn((message) => {
      queuedMessages = [...queuedMessages, message];
      events.push("enqueue");
    });
    const cloneFiles = vi.fn((files: File[]) => structuredClone(files));
    const clearQueuedInput = vi.fn(() => {
      currentFiles = [];
      events.push("clear-queued-input");
    });
    const stopResponse = vi.fn(async () => events.push("stop-response"));
    const waitForRender = vi.fn(async () => events.push("wait-for-render"));
    const prepareFinalInput = vi.fn(() => {
      const files = structuredClone(currentFiles);
      currentFiles = [];
      events.push("prepare-final-input");
      return files;
    });
    const submit = vi.fn(async () => {
      events.push("submit");
      return { status: "submitted" as const };
    });

    const options: Options = {
      prompt: "Summarize this report",
      history,
      bridgeRequest: null,
      isCurrentHistory,
      resolveChatId,
      ensureChatId,
      hasPendingFiles,
      uploadPendingFiles,
      setProcessing,
      notifyPreparationError,
      getQueuedMessages,
      getCurrentFiles,
      isAssistantGenerating,
      isMessageQueueEnabled,
      createRequestId,
      enqueueRequest,
      cloneFiles,
      clearQueuedInput,
      stopResponse,
      waitForRender,
      prepareFinalInput,
      submit,
      ...overrides,
    };

    return {
      options,
      history,
      bridgeRequest,
      events,
      isCurrentHistory,
      resolveChatId,
      uploadPendingFiles,
      setProcessing,
      notifyPreparationError,
      getCurrentFiles,
      isAssistantGenerating,
      isMessageQueueEnabled,
      enqueueRequest,
      cloneFiles,
      clearQueuedInput,
      stopResponse,
      waitForRender,
      prepareFinalInput,
      submit,
      getQueuedMessages: () => queuedMessages,
      getFiles: () => currentFiles,
      setFiles: (files: File[]) => (currentFiles = files),
    };
  };

  it("stops before dispatch when the history becomes stale during preparation", async () => {
    const harness = createHarness({ isCurrentHistory: vi.fn(() => false) });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
    expect(harness.resolveChatId).not.toHaveBeenCalled();
    expect(harness.submit).not.toHaveBeenCalled();
    expect(harness.setProcessing.mock.calls.map(([state]) => state)).toEqual([
      "uploading",
      "",
    ]);
  });

  it("does not dispatch when pending file uploads fail", async () => {
    const harness = createHarness({
      hasPendingFiles: vi.fn(() => true),
      uploadPendingFiles: vi.fn(async () => false),
    });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
    expect(harness.submit).not.toHaveBeenCalled();
    expect(harness.enqueueRequest).not.toHaveBeenCalled();
    expect(harness.setProcessing.mock.calls.map(([state]) => state)).toEqual([
      "uploading",
      "",
    ]);
  });

  it("reports preparation failures and restores processing state", async () => {
    const harness = createHarness({
      resolveChatId: vi.fn(async () => {
        throw new Error("chat resolution failed");
      }),
    });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
    expect(harness.notifyPreparationError).toHaveBeenCalledWith(
      expect.any(Error),
    );
    expect(harness.setProcessing.mock.calls.map(([state]) => state)).toEqual([
      "uploading",
      "",
    ]);
    expect(harness.submit).not.toHaveBeenCalled();
  });

  it("defers bridge requests and preserves the composer while files are transferring", async () => {
    const harness = createHarness({
      bridgeRequest: { userMessageId: "user-1", modelId: "model-1" },
      getQueuedMessages: vi.fn(() => [
        { files: [{ id: "file-2", status: "error" }] },
      ]),
    });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "deferred",
    });
    expect(harness.isAssistantGenerating).not.toHaveBeenCalled();
    expect(harness.enqueueRequest).not.toHaveBeenCalled();
    expect(harness.clearQueuedInput).not.toHaveBeenCalled();
    expect(harness.submit).not.toHaveBeenCalled();
  });

  it("queues the original attachment array behind pending transfers and clears the composer", async () => {
    const harness = createHarness();
    harness.setFiles([{ id: "file-2", status: "uploading" }]);
    const currentFiles = harness.getFiles();

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
    expect(harness.enqueueRequest).toHaveBeenCalledWith({
      id: "queued-1",
      prompt: "Summarize this report",
      files: currentFiles,
    });
    expect(harness.getQueuedMessages()[0].files).toBe(currentFiles);
    expect(harness.clearQueuedInput).toHaveBeenCalledOnce();
    expect(harness.submit).not.toHaveBeenCalled();
  });

  it("snapshots queued attachments while an assistant response is generating", async () => {
    const harness = createHarness({ isAssistantGenerating: vi.fn(() => true) });
    harness.setFiles([{ id: "file-2", status: "uploaded" }]);
    const currentFiles = harness.getFiles();

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
    const queuedFiles = harness.getQueuedMessages()[0].files;
    expect(queuedFiles).toEqual(currentFiles);
    expect(queuedFiles).not.toBe(currentFiles);
    expect(harness.cloneFiles).toHaveBeenCalledWith(currentFiles);
    expect(harness.clearQueuedInput).toHaveBeenCalledOnce();
  });

  it("interrupts before sending when queueing is disabled", async () => {
    const harness = createHarness({
      isAssistantGenerating: vi.fn(() => true),
      isMessageQueueEnabled: vi.fn(() => false),
    });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "submitted",
    });
    expect(harness.events.slice(-4)).toEqual([
      "stop-response",
      "wait-for-render",
      "prepare-final-input",
      "submit",
    ]);
    expect(harness.enqueueRequest).not.toHaveBeenCalled();
  });

  it("prepares the final input before sending an idle submission", async () => {
    const harness = createHarness();

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "submitted",
    });
    expect(harness.events.slice(-2)).toEqual(["prepare-final-input", "submit"]);
    expect(harness.submit).toHaveBeenCalledWith(
      "Summarize this report",
      [{ id: "file-1", status: "uploaded" }],
      null,
    );
  });

  it("rejects when the final send callback has no result", async () => {
    const harness = createHarness({ submit: vi.fn(async () => undefined) });

    await expect(executeChatSubmission(harness.options)).resolves.toEqual({
      status: "rejected",
    });
  });
});
