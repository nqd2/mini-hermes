import { describe, expect, it, vi } from "vitest";
import type { FilesystemFile } from "$lib/apis/terminal";
import type { ChatFileAskControllerDependencies } from "./chatFileAskController";
import { createChatFileAskController } from "./chatFileAskController";

const file: FilesystemFile = {
  type: "filesystem",
  id: "/workspace/report.xlsx",
  path: "/workspace/report.xlsx",
  url: "/workspace/report.xlsx",
  name: "report.xlsx",
  status: "processed",
};

const createHarness = (
  lastMessage: { role: string; done?: boolean } | null,
  overrides: Partial<ChatFileAskControllerDependencies> = {},
) => {
  const enqueueRequest = vi.fn();
  const submitPrompt = vi.fn(
    async (_prompt: string, _files: FilesystemFile[]) => undefined,
  );
  const controller = createChatFileAskController({
    getLastMessage: () => lastMessage,
    createRequestId: () => "request-1",
    cloneFile: (attachment) => structuredClone(attachment),
    enqueueRequest,
    submitPrompt,
    ...overrides,
  });
  return { controller, enqueueRequest, submitPrompt };
};

describe("chat file ask controller", () => {
  it("queues a cloned file request while an assistant response is still running", async () => {
    const harness = createHarness({ role: "assistant", done: false });

    await expect(
      harness.controller.askFile("Summarize this file", file),
    ).resolves.toBe("queued");

    expect(harness.enqueueRequest).toHaveBeenCalledWith({
      id: "request-1",
      prompt: "Summarize this file",
      files: [expect.objectContaining({ id: file.id })],
    });
    expect(harness.enqueueRequest.mock.calls[0][0].files[0]).not.toBe(file);
    expect(harness.submitPrompt).not.toHaveBeenCalled();
  });

  it("submits immediately when the latest assistant message is complete", async () => {
    const harness = createHarness({ role: "assistant", done: true });

    await expect(harness.controller.askFile("List sheets", file)).resolves.toBe(
      "submitted",
    );

    expect(harness.submitPrompt).toHaveBeenCalledWith("List sheets", [
      expect.objectContaining({ id: file.id }),
    ]);
    expect(harness.submitPrompt.mock.calls[0][1][0]).not.toBe(file);
    expect(harness.enqueueRequest).not.toHaveBeenCalled();
  });

  it("submits immediately when there is no current assistant response", async () => {
    const harness = createHarness(null);

    await expect(harness.controller.askFile("Read file", file)).resolves.toBe(
      "submitted",
    );

    expect(harness.submitPrompt).toHaveBeenCalledOnce();
    expect(harness.enqueueRequest).not.toHaveBeenCalled();
  });
});
