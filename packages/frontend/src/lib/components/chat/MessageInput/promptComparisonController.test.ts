import { describe, expect, it, vi } from "vitest";
import { createPromptComparisonController } from "./promptComparisonController";

const createHarness = (overrides: Record<string, any> = {}) => {
  const input = { setText: vi.fn() };
  const modal = vi.fn();
  let current = { chatId: "chat-1", input, prompt: "original" };
  const dependencies = {
    getCurrentContext: vi.fn(() => current),
    loadModal: vi.fn(async () => modal),
    onLoadingChange: vi.fn(),
    onOpen: vi.fn(),
    onError: vi.fn(),
    onApplied: vi.fn(),
    getStaleTargetError: vi.fn(() => new Error("stale prompt")),
    ...overrides,
  };
  return {
    input,
    modal,
    dependencies,
    controller: createPromptComparisonController(dependencies),
    setCurrent: (value: typeof current) => (current = value),
  };
};

describe("prompt comparison workflow", () => {
  it("opens the lazy-loaded modal for the current chat and prompt", async () => {
    const { controller, dependencies, input, modal } = createHarness();

    await controller.open();

    expect(dependencies.onOpen).toHaveBeenCalledWith({
      modal,
      target: { chatId: "chat-1", input, content: "original" },
    });
    expect(dependencies.onLoadingChange.mock.calls).toEqual([[true], [false]]);
  });

  it("ignores a modal load that completes after the chat or editor changed", async () => {
    let resolveModal: (value: () => void) => void = () => {};
    const loadModal = vi.fn(
      () => new Promise<() => void>((resolve) => (resolveModal = resolve)),
    );
    const { controller, dependencies, setCurrent } = createHarness({
      loadModal,
    });
    const pending = controller.open();
    setCurrent({
      chatId: "chat-2",
      input: { setText: vi.fn() },
      prompt: "next",
    });
    resolveModal(vi.fn());

    await pending;

    expect(dependencies.onOpen).not.toHaveBeenCalled();
  });

  it("allows only one concurrent modal load and always releases loading state", async () => {
    let resolveModal: (value: () => void) => void = () => {};
    const loadModal = vi.fn(
      () => new Promise<() => void>((resolve) => (resolveModal = resolve)),
    );
    const { controller, dependencies } = createHarness({ loadModal });

    const pending = controller.open();
    await controller.open();
    expect(loadModal).toHaveBeenCalledTimes(1);
    resolveModal(vi.fn());
    await pending;

    expect(dependencies.onLoadingChange.mock.calls).toEqual([[true], [false]]);
  });

  it("reports modal-load failure and allows a later retry", async () => {
    const failure = new Error("load failed");
    const loadModal = vi
      .fn()
      .mockRejectedValueOnce(failure)
      .mockResolvedValue(vi.fn());
    const { controller, dependencies } = createHarness({ loadModal });

    await controller.open();
    await controller.open();

    expect(dependencies.onError).toHaveBeenCalledWith(failure);
    expect(loadModal).toHaveBeenCalledTimes(2);
    expect(dependencies.onOpen).toHaveBeenCalledTimes(1);
  });

  it("applies only to the unchanged prompt target", async () => {
    const { controller, dependencies, input } = createHarness();

    expect(() => controller.apply("edited")).toThrow("stale prompt");
    await controller.open();
    controller.apply("edited");
    expect(input.setText).toHaveBeenCalledWith("edited");
    expect(dependencies.onApplied).toHaveBeenCalledOnce();
  });

  it("rejects applying after the prompt changes while the comparison is open", async () => {
    const { controller, input, setCurrent } = createHarness();
    await controller.open();
    setCurrent({ chatId: "chat-1", input, prompt: "changed elsewhere" });

    expect(() => controller.apply("edited")).toThrow("stale prompt");
    expect(input.setText).not.toHaveBeenCalled();
  });

  it("keeps the editor unchanged when the accepted text matches the captured prompt", async () => {
    const { controller, dependencies, input } = createHarness();
    await controller.open();

    controller.apply("original");

    expect(input.setText).not.toHaveBeenCalled();
    expect(dependencies.onApplied).toHaveBeenCalledOnce();
  });
});
