import { describe, expect, it, vi } from "vitest";
import { handleChatInteractionDialogEvent } from "./chatInteractionDialogHandler";

const makeDependencies = (overrides = {}) => ({
  showConfirmation: vi.fn(),
  showInput: vi.fn(),
  executeCode: vi.fn(async (code: string) => `result:${code}`),
  logExecutionError: vi.fn(),
  ...overrides,
});

describe("handleChatInteractionDialogEvent", () => {
  it("opens a confirmation dialog with the callback and interaction ID", () => {
    const dependencies = makeDependencies();
    const callback = vi.fn();
    const data = {
      interaction_id: "interaction-1",
      title: "Confirm?",
      message: "Continue?",
    };

    const result = handleChatInteractionDialogEvent(
      "confirmation",
      data,
      callback,
      dependencies,
    );

    expect(result).toBe(true);
    expect(dependencies.showConfirmation).toHaveBeenCalledWith(callback, data);
  });

  it("opens an input dialog and preserves its established fallback fields", () => {
    const dependencies = makeDependencies();
    const callback = vi.fn();
    const data = {
      interaction_id: "interaction-2",
      title: "Choose",
      message: "Pick one",
      placeholder: "Your choice",
      value: "first",
      input: { type: "select", options: ["first", "second"] },
    };

    const result = handleChatInteractionDialogEvent(
      "input",
      data,
      callback,
      dependencies,
    );

    expect(result).toBe(true);
    expect(dependencies.showInput).toHaveBeenCalledWith(callback, data);
  });

  it("executes code asynchronously and sends the result to its callback", async () => {
    const dependencies = makeDependencies();
    const callback = vi.fn();

    const result = handleChatInteractionDialogEvent(
      "execute",
      { code: "return 42" },
      callback,
      dependencies,
    );

    expect(result).toBeInstanceOf(Promise);
    await result;
    expect(dependencies.executeCode).toHaveBeenCalledWith("return 42");
    expect(callback).toHaveBeenCalledWith("result:return 42");
  });

  it("logs execution errors and still consumes the event", async () => {
    const error = new Error("bad script");
    const dependencies = makeDependencies({
      executeCode: vi.fn(async () => {
        throw error;
      }),
    });
    const callback = vi.fn();

    const result = handleChatInteractionDialogEvent(
      "execute",
      { code: "throw new Error()" },
      callback,
      dependencies,
    );

    await result;
    expect(dependencies.logExecutionError).toHaveBeenCalledWith(error);
    expect(callback).not.toHaveBeenCalled();
  });

  it("returns synchronously for event types it does not own", () => {
    const dependencies = makeDependencies();

    const result = handleChatInteractionDialogEvent(
      "chat:message:delta",
      {},
      undefined,
      dependencies,
    );

    expect(result).toBe(false);
    expect(result).not.toBeInstanceOf(Promise);
    expect(dependencies.showConfirmation).not.toHaveBeenCalled();
    expect(dependencies.showInput).not.toHaveBeenCalled();
  });
});
