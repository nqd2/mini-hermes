import {
  mergeChatHistoryMessages,
  type ChatHistorySnapshot,
  type ChatHistoryMergeOptions,
} from "./chatHistoryMerge";
import { reconcileLoadedChatTasks } from "./chatLoadTaskReconciliation";
import { messageHasPendingAskUser } from "./chatPendingInteraction";
import { findPendingToolApproval } from "./chatToolApproval";

/** Recover missed events from a fresh HTTP snapshot, preserving intervening live updates. */
export const recoverChatSnapshot = (
  history: ChatHistorySnapshot,
  incoming: ChatHistorySnapshot,
  baseline: ChatHistorySnapshot["messages"],
  options: ChatHistoryMergeOptions,
  pendingTaskIds: readonly string[] | null,
) => {
  const result = mergeChatHistoryMessages(history, incoming, options, baseline);
  const isOwnedPending = (message: Record<string, any>) =>
    message.role === "assistant" &&
    !message.done &&
    (message.user_id ?? options.fallbackOwnerId) === options.currentUserId;
  // A completion, new submission, or tool resume can race the snapshot request.
  // In that case live state owns task reconciliation.
  const pendingChanged = Object.entries(history.messages).some(
    ([id, message]) =>
      isOwnedPending(message) &&
      (baseline[id] !== message ||
        (message.meta?.task_id &&
          message.meta.task_id !== incoming.messages[id]?.meta?.task_id)),
  );
  let taskIds: string[] | null | undefined;
  if (
    options.recoverActive &&
    !options.generating &&
    !pendingChanged &&
    pendingTaskIds !== null
  ) {
    const messages = result.history.messages;
    // With multiple model responses, one completed column does not finish the others.
    const pending = Object.values(messages).find(isOwnedPending);
    reconcileLoadedChatTasks({
      pendingTaskIds,
      currentMessage: pending ?? messages[result.history.currentId ?? ""],
      snapshotTaskIds: options.taskIds ?? null,
      getCurrentTaskIds: () => options.taskIds ?? null,
      canReconcile: () => true,
      messages,
      currentUserId: options.currentUserId,
      fallbackOwnerId: options.fallbackOwnerId,
      messageHasPendingAskUser: (message) =>
        messageHasPendingAskUser(message) ||
        Boolean(
          findPendingToolApproval(
            [message],
            options.currentUserId ?? undefined,
            options.fallbackOwnerId ?? undefined,
          ),
        ),
      setTaskIds: (ids) => {
        taskIds = ids;
      },
      markHistoryChanged: () => {},
    });
  }
  const current = result.history.messages[result.history.currentId ?? ""];
  return {
    ...result,
    taskIds,
    // Only advance a queue after this recovery actually settled the active response.
    processQueue:
      taskIds === null &&
      current?.role === "assistant" &&
      current.done === true &&
      !Object.values(result.history.messages).some(isOwnedPending),
  };
};
