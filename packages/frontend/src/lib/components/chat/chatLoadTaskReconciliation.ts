type ChatMessage = Record<string, any>;

export type ChatLoadTaskReconciliationOptions = {
  pendingTaskIds: readonly string[];
  currentMessage: ChatMessage | null | undefined;
  snapshotTaskIds: readonly string[] | null;
  getCurrentTaskIds: () => readonly string[] | null;
  canReconcile: () => boolean;
  messages: Record<string, ChatMessage>;
  currentUserId: string | null | undefined;
  fallbackOwnerId: string | null | undefined;
  messageHasPendingAskUser: (message: ChatMessage) => boolean;
  setTaskIds: (taskIds: string[] | null) => unknown;
  markHistoryChanged: () => void;
};

/** Reconciles persisted task IDs with the active response after a chat load. */
export const reconcileLoadedChatTasks = (
  options: ChatLoadTaskReconciliationOptions,
): boolean => {
  if (
    !options.canReconcile() ||
    options.getCurrentTaskIds() !== options.snapshotTaskIds
  ) {
    return false;
  }

  const responseComplete =
    options.currentMessage?.role === "assistant" &&
    options.currentMessage?.done;
  if (options.pendingTaskIds.length > 0 && !responseComplete) {
    options.setTaskIds([...options.pendingTaskIds]);
    return false;
  }

  options.setTaskIds(null);
  if (options.pendingTaskIds.length > 0) return false;

  let historyChanged = false;
  for (const message of Object.values(options.messages)) {
    if (
      message?.role === "assistant" &&
      (message.user_id ?? options.fallbackOwnerId) === options.currentUserId &&
      !message.done &&
      !options.messageHasPendingAskUser(message)
    ) {
      message.done = true;
      historyChanged = true;
    }
  }

  if (historyChanged) options.markHistoryChanged();
  return historyChanged;
};
