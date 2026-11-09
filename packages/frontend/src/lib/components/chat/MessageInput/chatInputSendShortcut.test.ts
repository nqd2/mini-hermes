import { describe, expect, it } from "vitest";
import { isChatInputSendShortcut } from "./chatInputSendShortcut";

const keyEvent = (overrides: Partial<KeyboardEvent> = {}) =>
  ({
    key: "Enter",
    keyCode: 13,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...overrides,
  }) as KeyboardEvent;

describe("chat input send shortcut", () => {
  it("sends on Enter and Ctrl+Enter when Ctrl+Enter mode is disabled", () => {
    expect(isChatInputSendShortcut(keyEvent(), false)).toBe(true);
    expect(isChatInputSendShortcut(keyEvent({ ctrlKey: true }), false)).toBe(
      true,
    );
  });

  it("requires Ctrl or Command when Ctrl+Enter mode is enabled", () => {
    expect(isChatInputSendShortcut(keyEvent(), true)).toBe(false);
    expect(isChatInputSendShortcut(keyEvent({ ctrlKey: true }), true)).toBe(
      true,
    );
    expect(isChatInputSendShortcut(keyEvent({ metaKey: true }), true)).toBe(
      true,
    );
  });

  it("supports IME Enter keyCode and never sends on Shift+Enter", () => {
    expect(
      isChatInputSendShortcut(keyEvent({ key: "Process", keyCode: 13 }), false),
    ).toBe(true);
    expect(isChatInputSendShortcut(keyEvent({ shiftKey: true }), false)).toBe(
      false,
    );
    expect(
      isChatInputSendShortcut(keyEvent({ key: "Escape", keyCode: 27 }), false),
    ).toBe(false);
  });
});
