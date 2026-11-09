import { describe, expect, it, vi } from "vitest";
import { createChatCommandController } from "./chatCommandController";

const makeDependencies = (overrides = {}) => ({
  clearInput: vi.fn(),
  compact: vi.fn(async () => {}),
  showStatus: vi.fn(),
  openComments: vi.fn(),
  isSideChatAllowed: vi.fn(() => true),
  canStartSideChat: vi.fn(() => true),
  hasSingleModelSelected: vi.fn(() => true),
  getParentChatId: vi.fn(() => "chat-1"),
  openSideChat: vi.fn(),
  fork: vi.fn(async () => {}),
  switchModel: vi.fn(),
  notifyError: vi.fn(),
  translate: vi.fn((text: string) => text),
  ...overrides,
});

describe("createChatCommandController", () => {
  it("handles built-in commands and clears input before running them", async () => {
    const dependencies = makeDependencies();
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("  /compact  ")).toBe(true);
    expect(dependencies.clearInput.mock.invocationCallOrder[0]).toBeLessThan(
      dependencies.compact.mock.invocationCallOrder[0],
    );
    expect(dependencies.showStatus).not.toHaveBeenCalled();

    expect(await controller.handle("/status")).toBe(true);
    expect(dependencies.showStatus).toHaveBeenCalledOnce();
    expect(await controller.handle("/comments")).toBe(true);
    expect(dependencies.openComments).toHaveBeenCalledOnce();
  });

  it("rejects side chat when the user lacks permission", async () => {
    const dependencies = makeDependencies({
      isSideChatAllowed: vi.fn(() => false),
    });
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("/side summarize this")).toBe(true);
    expect(dependencies.notifyError).toHaveBeenCalledWith(
      "You do not have permission to use side chat.",
    );
    expect(dependencies.openSideChat).not.toHaveBeenCalled();
    expect(dependencies.clearInput).not.toHaveBeenCalled();
  });

  it("opens side chat with the current chat and command text", async () => {
    const dependencies = makeDependencies();
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("/BTW  compare these results ")).toBe(true);
    expect(dependencies.clearInput).toHaveBeenCalledOnce();
    expect(dependencies.openSideChat).toHaveBeenCalledWith(
      "chat-1",
      "compare these results",
    );
  });

  it("lets an unavailable side-chat context fall through as a normal prompt", async () => {
    const dependencies = makeDependencies({
      canStartSideChat: vi.fn(() => false),
    });
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("/side summarize this")).toBe(false);
    expect(dependencies.clearInput).not.toHaveBeenCalled();
    expect(dependencies.openSideChat).not.toHaveBeenCalled();
  });

  it("rejects side chat unless exactly one model is selected", async () => {
    const dependencies = makeDependencies({
      hasSingleModelSelected: vi.fn(() => false),
    });
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("/side summarize this")).toBe(true);
    expect(dependencies.notifyError).toHaveBeenCalledWith(
      "Select only one model to open a side chat.",
    );
    expect(dependencies.clearInput).not.toHaveBeenCalled();
  });

  it("dispatches fork and model commands with their established input behavior", async () => {
    const dependencies = makeDependencies();
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("/fork")).toBe(true);
    expect(dependencies.fork).toHaveBeenCalledOnce();

    expect(await controller.handle("/model  qwen3.7-plus  ")).toBe(true);
    expect(dependencies.switchModel).toHaveBeenCalledWith("qwen3.7-plus");
    expect(await controller.handle("/model")).toBe(true);
    expect(dependencies.switchModel).toHaveBeenLastCalledWith("");
  });

  it("does not consume ordinary prompts or unknown commands", async () => {
    const dependencies = makeDependencies();
    const controller = createChatCommandController(dependencies);

    expect(await controller.handle("explain this document")).toBe(false);
    expect(await controller.handle("/unknown command")).toBe(false);
  });

  it("returns synchronously for ordinary prompts so submit guards run without a microtask gap", () => {
    const controller = createChatCommandController(makeDependencies());

    const result = controller.handle("explain this document");

    expect(result).toBe(false);
    expect(result).not.toBeInstanceOf(Promise);
  });
});
