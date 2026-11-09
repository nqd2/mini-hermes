import { describe, expect, it, vi } from "vitest";
import { createManualContextCompactionController } from "./chatManualCompaction";

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  isEnabled: vi.fn(() => true),
  getChatId: vi.fn(() => "chat-1"),
  getCurrentMessage: vi.fn(() => ({
    id: "message-1",
    message: { role: "user" },
  })),
  isGenerating: vi.fn(() => false),
  hasPendingTasks: vi.fn(() => false),
  getModelId: vi.fn(() => "model-1"),
  isInProgress: vi.fn(() => false),
  markInProgress: vi.fn(),
  clearStatus: vi.fn(),
  markError: vi.fn(),
  getToken: vi.fn(() => "token"),
  translate: vi.fn((key: string) => key),
  notify: vi.fn(),
  compactChat: vi.fn(async () => ({
    compacted: true,
    context_usage: { tokens: 100 },
  })),
  setServerContextUsage: vi.fn(),
  loadChat: vi.fn(async () => {}),
  getErrorMessage: vi.fn((error: any, fallback: string) =>
    String(error?.detail ?? error?.message ?? fallback),
  ),
  focus: vi.fn(),
  ...overrides,
});

describe("createManualContextCompactionController", () => {
  it("compacts the active chat, updates usage, reloads it, clears progress and restores focus", async () => {
    const dependencies = makeDependencies();
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.markInProgress).toHaveBeenCalledWith("message-1");
    expect(dependencies.compactChat).toHaveBeenCalledWith(
      "token",
      "chat-1",
      "model-1",
    );
    expect(dependencies.setServerContextUsage).toHaveBeenCalledWith({
      tokens: 100,
    });
    expect(dependencies.loadChat).toHaveBeenCalledOnce();
    expect(dependencies.clearStatus).toHaveBeenCalledWith("message-1");
    expect(dependencies.focus).toHaveBeenCalledOnce();
  });

  it("blocks compaction when generation or a task is still active", async () => {
    const dependencies = makeDependencies({
      isGenerating: vi.fn(() => false),
      hasPendingTasks: vi.fn(() => true),
    });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.notify).toHaveBeenCalledWith(
      "warning",
      "Wait for the current response to finish before compacting.",
    );
    expect(dependencies.compactChat).not.toHaveBeenCalled();
    expect(dependencies.focus).not.toHaveBeenCalled();
  });

  it("blocks compaction while the current assistant response is unfinished", async () => {
    const dependencies = makeDependencies({
      getCurrentMessage: vi.fn(() => ({
        id: "message-1",
        message: { role: "assistant", done: false },
      })),
    });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.notify).toHaveBeenCalledWith(
      "warning",
      "Wait for the current response to finish before compacting.",
    );
    expect(dependencies.compactChat).not.toHaveBeenCalled();
    expect(dependencies.focus).not.toHaveBeenCalled();
  });

  it("does not duplicate an in-progress compaction", async () => {
    const dependencies = makeDependencies({ isInProgress: vi.fn(() => true) });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.markInProgress).not.toHaveBeenCalled();
    expect(dependencies.compactChat).not.toHaveBeenCalled();
    expect(dependencies.focus).not.toHaveBeenCalled();
  });

  it("maps a skipped result to its localized reason and clears progress after refresh", async () => {
    const dependencies = makeDependencies({
      compactChat: vi.fn(async () => ({
        compacted: false,
        reason: "too_short",
      })),
    });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.notify).toHaveBeenCalledWith(
      "message",
      "Chat is too short to compact",
    );
    expect(dependencies.loadChat).toHaveBeenCalledOnce();
    expect(dependencies.clearStatus).toHaveBeenCalledWith("message-1");
  });

  it("stores an error only for the chat that started the request", async () => {
    const error = new Error("network failed");
    const dependencies = makeDependencies({
      compactChat: vi.fn(async () => {
        throw error;
      }),
    });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.markError).toHaveBeenCalledWith(
      "message-1",
      "network failed",
    );
    expect(dependencies.focus).toHaveBeenCalledOnce();
  });

  it("ignores stale results after navigation to another chat", async () => {
    const getChatId = vi
      .fn()
      .mockReturnValueOnce("chat-1")
      .mockReturnValue("chat-2");
    const dependencies = makeDependencies({ getChatId });
    const controller = createManualContextCompactionController(dependencies);

    await controller.compact();

    expect(dependencies.setServerContextUsage).not.toHaveBeenCalled();
    expect(dependencies.loadChat).not.toHaveBeenCalled();
    expect(dependencies.clearStatus).not.toHaveBeenCalled();
    expect(dependencies.focus).toHaveBeenCalledOnce();
  });
});
