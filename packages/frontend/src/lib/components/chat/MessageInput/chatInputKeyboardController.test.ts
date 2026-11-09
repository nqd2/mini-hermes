import { describe, expect, it, vi } from "vitest";
import { createChatInputKeyboardController } from "./chatInputKeyboardController";

const keyboardEvent = (key: string) =>
  ({ key, preventDefault: vi.fn() }) as unknown as KeyboardEvent;

const createHarness = () => {
  let shortcutsEnabled = true;
  let recording = false;
  let inputId = "chat-input";
  let activeComposerId: string | null = "chat-input";
  const setShiftKey = vi.fn();
  const clickElementById = vi.fn();
  const setDragged = vi.fn();
  const logEscape = vi.fn();
  const controller = createChatInputKeyboardController({
    areShortcutsEnabled: () => shortcutsEnabled,
    isRecording: () => recording,
    getInputId: () => inputId,
    getActiveComposerId: () => activeComposerId,
    matchKeybinding: (event) =>
      event.key === "dictation" ? "dictation" : null,
    dictationKeybinding: "dictation",
    setShiftKey,
    clickElementById,
    setDragged,
    logEscape,
  });

  return {
    controller,
    setShortcutsEnabled: (value: boolean) => (shortcutsEnabled = value),
    setRecording: (value: boolean) => (recording = value),
    setInputId: (value: string) => (inputId = value),
    setActiveComposerId: (value: string | null) => (activeComposerId = value),
    setShiftKey,
    clickElementById,
    setDragged,
    logEscape,
  };
};

describe("chat input keyboard controller", () => {
  it("tracks Shift across keydown, keyup and window blur", () => {
    const harness = createHarness();

    harness.controller.onKeyDown(keyboardEvent("Shift"));
    harness.controller.onKeyUp(keyboardEvent("Shift"));
    harness.controller.onBlur();

    expect(harness.setShiftKey.mock.calls).toEqual([[true], [false], [false]]);
  });

  it("toggles dictation only for the active composer or while recording", () => {
    const harness = createHarness();
    const inactiveEvent = keyboardEvent("dictation");
    harness.setActiveComposerId("other-composer");
    harness.controller.onKeyDown(inactiveEvent);
    expect(inactiveEvent.preventDefault).not.toHaveBeenCalled();

    harness.setRecording(true);
    const confirmEvent = keyboardEvent("dictation");
    harness.controller.onKeyDown(confirmEvent);
    expect(confirmEvent.preventDefault).toHaveBeenCalledOnce();
    expect(harness.clickElementById).toHaveBeenCalledWith(
      "confirm-recording-button",
    );
  });

  it("uses composer-specific voice button ids for a secondary input", () => {
    const harness = createHarness();
    harness.setInputId("side-chat-1");
    harness.setActiveComposerId("side-chat-1");

    const event = keyboardEvent("dictation");
    harness.controller.onKeyDown(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(harness.clickElementById).toHaveBeenCalledWith(
      "side-chat-1-voice-input-button",
    );
  });

  it("honors disabled shortcuts while Escape still clears drag state", () => {
    const harness = createHarness();
    harness.setShortcutsEnabled(false);
    const dictationEvent = keyboardEvent("dictation");
    harness.controller.onKeyDown(dictationEvent);
    expect(dictationEvent.preventDefault).not.toHaveBeenCalled();
    expect(harness.clickElementById).not.toHaveBeenCalled();

    harness.controller.onKeyDown(keyboardEvent("Escape"));

    expect(harness.logEscape).toHaveBeenCalledOnce();
    expect(harness.setDragged).toHaveBeenCalledWith(false);
  });
});
