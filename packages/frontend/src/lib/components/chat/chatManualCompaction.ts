type CurrentMessage = { role?: string; done?: boolean };

export type ManualContextCompactionDependencies = {
  isEnabled: () => boolean;
  getChatId: () => string | null | undefined;
  getCurrentMessage: () => {
    id: string | null | undefined;
    message?: CurrentMessage;
  };
  isGenerating: () => boolean;
  hasPendingTasks: () => boolean;
  getModelId: () => string | undefined;
  isInProgress: (messageId: string) => boolean;
  markInProgress: (messageId: string) => void;
  clearStatus: (messageId: string) => void;
  markError: (messageId: string, error: string) => void;
  getToken: () => string;
  translate: (key: string) => string;
  notify: (kind: "message" | "warning", text: string) => void;
  compactChat: (
    token: string,
    chatId: string,
    modelId: string | undefined,
  ) => Promise<
    | { compacted?: boolean; reason?: string; context_usage?: unknown }
    | null
    | undefined
  >;
  setServerContextUsage: (usage: unknown) => void;
  loadChat: () => Promise<unknown>;
  getErrorMessage: (error: unknown, fallback: string) => string;
  focus: () => void;
};

export const createManualContextCompactionController = (
  dependencies: ManualContextCompactionDependencies,
) => {
  const compact = async (): Promise<void> => {
    if (!dependencies.isEnabled()) {
      dependencies.notify(
        "message",
        dependencies.translate("Context compaction is disabled"),
      );
      return;
    }

    const chatId = dependencies.getChatId();
    const currentMessage = dependencies.getCurrentMessage();
    if (!chatId || !currentMessage.id) {
      dependencies.notify(
        "message",
        dependencies.translate("No chat to compact"),
      );
      return;
    }

    if (
      dependencies.isGenerating() ||
      dependencies.hasPendingTasks() ||
      (currentMessage.message?.role === "assistant" &&
        !currentMessage.message.done)
    ) {
      dependencies.notify(
        "warning",
        dependencies.translate(
          "Wait for the current response to finish before compacting.",
        ),
      );
      return;
    }

    const messageId = currentMessage.id;
    if (dependencies.isInProgress(messageId)) return;
    dependencies.markInProgress(messageId);

    try {
      const result = await dependencies.compactChat(
        dependencies.getToken(),
        chatId,
        dependencies.getModelId(),
      );
      if (dependencies.getChatId() !== chatId) return;
      dependencies.setServerContextUsage(result?.context_usage);

      if (!result?.compacted) {
        const reason =
          result?.reason === "too_short"
            ? "Chat is too short to compact"
            : result?.reason === "empty"
              ? "No chat to compact"
              : result?.reason === "disabled"
                ? "Context compaction is disabled"
                : "Nothing to compact";
        dependencies.notify("message", dependencies.translate(reason));
      }

      await dependencies.loadChat();
      dependencies.clearStatus(messageId);
    } catch (error) {
      if (dependencies.getChatId() === chatId) {
        dependencies.markError(
          messageId,
          dependencies.getErrorMessage(
            error,
            dependencies.translate("Context compaction failed"),
          ),
        );
      }
    } finally {
      dependencies.focus();
    }
  };

  return { compact };
};
