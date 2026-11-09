import { describe, expect, it, vi } from "vitest";
import {
  handleChatMessageError,
  handleChatTaskCancellation,
} from "./chatStreamTaskEvents";

const cancellationHarness = (
  options: { shared?: boolean; current?: boolean; parent?: boolean } = {},
) => {
  const events: string[] = [];
  const currentId = options.current === false ? "other-message" : "message-1";
  const message = {
    id: "message-1",
    parentId: options.parent === false ? "missing-parent" : "parent-1",
    done: false,
  };
  const childIds = ["sibling-1", "sibling-2"];
  const resolvePendingInteractions = vi.fn(() =>
    events.push("resolve-interactions"),
  );
  const cancelBridge = vi.fn(() => events.push("cancel-bridge"));
  const clearContextCompaction = vi.fn((id: string) =>
    events.push(`clear:${id}`),
  );
  const markMessageDone = vi.fn((id: string) => events.push(`done:${id}`));
  const setTaskIds = vi.fn(() => events.push("clear-task-ids"));
  const processNextInQueue = vi.fn(async () => events.push("process-next"));
  const getParentChildren = vi.fn(() =>
    options.parent === false ? null : childIds,
  );

  return {
    events,
    message,
    childIds,
    resolvePendingInteractions,
    cancelBridge,
    clearContextCompaction,
    markMessageDone,
    setTaskIds,
    processNextInQueue,
    getParentChildren,
    options: {
      chatId: "chat-1",
      messageId: "message-1",
      shared: options.shared ?? false,
      currentMessageId: () => currentId,
      message,
      output: [{ type: "function_call_output", call_id: "call-1" }],
      resolvePendingInteractions,
      cancelBridge,
      clearContextCompaction,
      getParentChildren,
      markMessageDone,
      setTaskIds,
      processNextInQueue,
    },
  };
};

describe("handleChatTaskCancellation", () => {
  it("resolves interactions, finishes sibling messages, clears task state and advances the queue", async () => {
    const harness = cancellationHarness();

    await handleChatTaskCancellation(harness.options);

    expect(harness.resolvePendingInteractions).toHaveBeenCalledWith(
      "message-1",
    );
    expect(harness.cancelBridge).toHaveBeenCalledWith("message-1");
    expect(harness.message).toMatchObject({
      bridgeCancelled: true,
      done: true,
      output: harness.options.output,
    });
    expect(harness.markMessageDone.mock.calls.map(([id]) => id)).toEqual(
      harness.childIds,
    );
    expect(harness.clearContextCompaction.mock.calls.map(([id]) => id)).toEqual(
      ["message-1", ...harness.childIds],
    );
    expect(harness.setTaskIds).toHaveBeenCalledWith(null);
    expect(harness.events.slice(-2)).toEqual([
      "clear:sibling-2",
      "process-next",
    ]);
  });

  it("does not clear the active task or advance the queue for shared or non-current messages", async () => {
    for (const options of [{ shared: true }, { current: false }]) {
      const harness = cancellationHarness(options);

      await handleChatTaskCancellation(harness.options);

      expect(harness.message.done).toBe(true);
      expect(harness.setTaskIds).not.toHaveBeenCalled();
      expect(harness.processNextInQueue).not.toHaveBeenCalled();
      expect(harness.markMessageDone).not.toHaveBeenCalled();
    }
  });

  it("finishes and advances a current message even when its parent is missing", async () => {
    const harness = cancellationHarness({ parent: false });

    await expect(
      handleChatTaskCancellation(harness.options),
    ).resolves.toBeUndefined();

    expect(harness.message.done).toBe(true);
    expect(harness.markMessageDone).not.toHaveBeenCalled();
    expect(harness.setTaskIds).toHaveBeenCalledWith(null);
    expect(harness.processNextInQueue).toHaveBeenCalledWith("chat-1");
  });
});

describe("handleChatMessageError", () => {
  it("marks shared failures complete without local queue effects", async () => {
    const message = { id: "message-1", done: false };
    const handleOpenAIError = vi.fn();
    const clearContextCompaction = vi.fn();
    const processNextInQueue = vi.fn();

    await handleChatMessageError({
      chatId: "chat-1",
      messageId: "message-1",
      shared: true,
      error: "upstream failed",
      message,
      currentMessageId: () => "message-1",
      handleOpenAIError,
      clearContextCompaction,
      processNextInQueue,
    });

    expect(message).toMatchObject({ error: "upstream failed", done: true });
    expect(handleOpenAIError).not.toHaveBeenCalled();
    expect(clearContextCompaction).not.toHaveBeenCalled();
    expect(processNextInQueue).not.toHaveBeenCalled();
  });

  it("clears context state and advances the queue after an incomplete local response fails", async () => {
    const events: string[] = [];
    const message = { id: "message-1", done: false };
    const clearContextCompaction = vi.fn(() => events.push("clear-context"));
    const handleOpenAIError = vi.fn(() => events.push("handle-error"));
    const processNextInQueue = vi.fn(async () => events.push("process-next"));

    await handleChatMessageError({
      chatId: "chat-1",
      messageId: "message-1",
      shared: false,
      error: "upstream failed",
      message,
      currentMessageId: () => "message-1",
      handleOpenAIError,
      clearContextCompaction,
      processNextInQueue,
    });

    expect(events).toEqual(["handle-error", "clear-context", "process-next"]);
    expect(clearContextCompaction).toHaveBeenCalledWith("message-1");
    expect(processNextInQueue).toHaveBeenCalledWith("chat-1");
  });

  it("does not restart the queue when the response was already complete", async () => {
    const message = { id: "message-1", done: true };
    const clearContextCompaction = vi.fn();
    const processNextInQueue = vi.fn();

    await handleChatMessageError({
      chatId: "chat-1",
      messageId: "message-1",
      shared: false,
      error: "late failure",
      message,
      currentMessageId: () => "message-1",
      handleOpenAIError: vi.fn(),
      clearContextCompaction,
      processNextInQueue,
    });

    expect(clearContextCompaction).not.toHaveBeenCalled();
    expect(processNextInQueue).not.toHaveBeenCalled();
  });
});
