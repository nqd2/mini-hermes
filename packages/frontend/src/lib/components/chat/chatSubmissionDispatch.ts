export type ChatSubmissionDispatchOptions = {
  isBridgeRequest: boolean;
  hasPendingFileTransfers: boolean;
  assistantGenerating: boolean;
  messageQueueEnabled: boolean;
};

export type ChatSubmissionDispatchDecision =
  | { action: "send" }
  | { action: "interrupt" }
  | { action: "defer"; reason: "file-transfer" | "assistant-generating" }
  | { action: "queue"; reason: "file-transfer"; snapshotFiles: false }
  | { action: "queue"; reason: "assistant-generating"; snapshotFiles: true };

export const hasPendingChatFileTransfers = (
  queuedMessages: readonly { files?: readonly { status?: string }[] | null }[],
  currentFiles: readonly { status?: string }[],
) => {
  const containsIncompleteFile = (
    files: readonly { status?: string }[] | null | undefined,
  ) =>
    files?.some((file) => ["uploading", "error"].includes(file.status ?? "")) ??
    false;

  return (
    queuedMessages.some((message) => containsIncompleteFile(message.files)) ||
    containsIncompleteFile(currentFiles)
  );
};

export const getChatSubmissionDispatchDecision = (
  options: ChatSubmissionDispatchOptions,
): ChatSubmissionDispatchDecision => {
  if (options.hasPendingFileTransfers) {
    return options.isBridgeRequest
      ? { action: "defer", reason: "file-transfer" }
      : { action: "queue", reason: "file-transfer", snapshotFiles: false };
  }
  if (options.assistantGenerating) {
    if (options.isBridgeRequest)
      return { action: "defer", reason: "assistant-generating" };
    if (options.messageQueueEnabled) {
      return {
        action: "queue",
        reason: "assistant-generating",
        snapshotFiles: true,
      };
    }
    return { action: "interrupt" };
  }
  return { action: "send" };
};
