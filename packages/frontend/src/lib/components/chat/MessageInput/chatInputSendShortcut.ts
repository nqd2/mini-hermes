type KeyboardShortcutEvent = Pick<
  KeyboardEvent,
  "key" | "keyCode" | "ctrlKey" | "metaKey" | "shiftKey"
>;

/** Returns whether this Enter key event should submit the chat prompt. */
export const isChatInputSendShortcut = (
  event: KeyboardShortcutEvent,
  ctrlEnterToSend: boolean,
): boolean => {
  const enterPressed = event.key === "Enter" || event.keyCode === 13;
  if (!enterPressed || event.shiftKey) return false;

  return !ctrlEnterToSend || event.ctrlKey || event.metaKey;
};
