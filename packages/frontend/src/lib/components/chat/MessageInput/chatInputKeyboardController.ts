export type ChatInputKeyboardControllerDependencies = {
  areShortcutsEnabled: () => boolean;
  isRecording: () => boolean;
  getInputId: () => string;
  getActiveComposerId: () => string | null;
  matchKeybinding: (event: KeyboardEvent) => unknown;
  dictationKeybinding: unknown;
  setShiftKey: (value: boolean) => void;
  clickElementById: (id: string) => void;
  setDragged: (value: boolean) => void;
  logEscape: () => void;
};

/** Coordinates global keyboard shortcuts shared by the chat and side-chat composers. */
export const createChatInputKeyboardController = (
  dependencies: ChatInputKeyboardControllerDependencies,
) => {
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Shift") {
      dependencies.setShiftKey(true);
    }

    const inputId = dependencies.getInputId();
    if (
      dependencies.areShortcutsEnabled() &&
      (dependencies.isRecording() ||
        (dependencies.getActiveComposerId() ?? "chat-input") === inputId) &&
      dependencies.matchKeybinding(event) === dependencies.dictationKeybinding
    ) {
      event.preventDefault();
      const buttonId = dependencies.isRecording()
        ? inputId === "chat-input"
          ? "confirm-recording-button"
          : `${inputId}-confirm-recording-button`
        : inputId === "chat-input"
          ? "voice-input-button"
          : `${inputId}-voice-input-button`;
      dependencies.clickElementById(buttonId);
      return;
    }

    if (event.key === "Escape") {
      dependencies.logEscape();
      dependencies.setDragged(false);
    }
  };

  const onKeyUp = (event: KeyboardEvent) => {
    if (event.key === "Shift") {
      dependencies.setShiftKey(false);
    }
  };

  const onBlur = () => dependencies.setShiftKey(false);

  return { onKeyDown, onKeyUp, onBlur };
};
