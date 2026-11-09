import { describe, expect, it, vi } from "vitest";
import type { ChatInputPasteControllerDependencies } from "./chatInputPasteController";
import { createChatInputPasteController } from "./chatInputPasteController";

const createHarness = (
  overrides: Partial<ChatInputPasteControllerDependencies> = {},
) => {
  let largeTextAsFileEnabled = true;
  let shiftPressed = false;
  const uploadTextFile = vi.fn(async () => undefined);
  const processInputFiles = vi.fn(async () => undefined);
  const createTextFile = vi.fn(
    (text: string) => ({ name: "pasted.txt", text }) as unknown as File,
  );
  const controller = createChatInputPasteController({
    isLargeTextAsFileEnabled: () => largeTextAsFileEnabled,
    isShiftPressed: () => shiftPressed,
    maximumTextLength: 10,
    createTextFile,
    uploadTextFile,
    processInputFiles,
    ...overrides,
  });

  return {
    controller,
    uploadTextFile,
    processInputFiles,
    createTextFile,
    setLargeTextAsFileEnabled: (value: boolean) =>
      (largeTextAsFileEnabled = value),
    setShiftPressed: (value: boolean) => (shiftPressed = value),
  };
};

const pasteEvent = (
  items: Array<{ type: string; getAsFile: () => File | null }>,
  text = "",
) => {
  const preventDefault = vi.fn();
  return {
    event: {
      preventDefault,
      clipboardData: { items, getData: () => text },
    },
    preventDefault,
  };
};

describe("chat input paste controller", () => {
  it("uploads long plain text as a full-context text file when enabled", async () => {
    const harness = createHarness();
    const { event, preventDefault } = pasteEvent(
      [{ type: "text/plain", getAsFile: () => null }],
      "long enough text",
    );

    await harness.controller.onPaste(event);

    expect(preventDefault).toHaveBeenCalledOnce();
    expect(harness.createTextFile).toHaveBeenCalledWith("long enough text");
    expect(harness.uploadTextFile).toHaveBeenCalledWith({
      name: "pasted.txt",
      text: "long enough text",
    });
    expect(harness.processInputFiles).not.toHaveBeenCalled();
  });

  it("leaves short text, disabled conversion and Shift+paste in the editor", async () => {
    const harness = createHarness();
    const shortPaste = pasteEvent(
      [{ type: "text/plain", getAsFile: () => null }],
      "short",
    );
    await harness.controller.onPaste(shortPaste.event);

    harness.setLargeTextAsFileEnabled(false);
    const disabledPaste = pasteEvent(
      [{ type: "text/plain", getAsFile: () => null }],
      "long enough text",
    );
    await harness.controller.onPaste(disabledPaste.event);

    harness.setLargeTextAsFileEnabled(true);
    harness.setShiftPressed(true);
    const shiftedPaste = pasteEvent(
      [{ type: "text/plain", getAsFile: () => null }],
      "long enough text",
    );
    await harness.controller.onPaste(shiftedPaste.event);

    expect(shortPaste.preventDefault).not.toHaveBeenCalled();
    expect(disabledPaste.preventDefault).not.toHaveBeenCalled();
    expect(shiftedPaste.preventDefault).not.toHaveBeenCalled();
    expect(harness.uploadTextFile).not.toHaveBeenCalled();
  });

  it("sends pasted files through the composer file pipeline", async () => {
    const harness = createHarness();
    const file = { name: "table.xlsx" } as File;
    const { event, preventDefault } = pasteEvent([
      { type: "application/x-file", getAsFile: () => file },
    ]);

    await harness.controller.onPaste(event);

    expect(harness.processInputFiles).toHaveBeenCalledWith([file]);
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(harness.uploadTextFile).not.toHaveBeenCalled();
  });

  it("does nothing for clipboard items without a file or eligible text", async () => {
    const harness = createHarness();
    const { event, preventDefault } = pasteEvent([
      { type: "image/png", getAsFile: () => null },
    ]);

    await harness.controller.onPaste(event);

    expect(preventDefault).not.toHaveBeenCalled();
    expect(harness.processInputFiles).not.toHaveBeenCalled();
    expect(harness.uploadTextFile).not.toHaveBeenCalled();
  });
});
