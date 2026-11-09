type ChatMessage = Record<string, any>;
type ChatMessages = Record<string, ChatMessage>;
type NotificationAdapters = {
  success: (content: unknown) => unknown;
  error: (content: unknown) => unknown;
  warning: (content: unknown) => unknown;
  info: (content: unknown) => unknown;
};

export type ChatMessageAuxiliaryEventResult =
  "message-updated" | "history-patched" | null;

/** Applies stream events that update message metadata, citations, notifications, or outlet text. */
export const handleChatMessageAuxiliaryEvent = (
  type: string,
  data: any,
  message: ChatMessage | null,
  messages: ChatMessages,
  notifications: NotificationAdapters,
): ChatMessageAuxiliaryEventResult => {
  if (type === "chat:outlet") {
    for (const item of data.messages ?? []) {
      if (item?.id && messages[item.id]) {
        const existing = messages[item.id];
        if (existing.content !== item.content) {
          messages[item.id] = {
            ...existing,
            originalContent: existing.content,
            ...item,
          };
        }
      }
    }
    return "history-patched";
  }

  if (type === "chat:message:favorite" && message) {
    message.favorite = data.favorite;
    return "message-updated";
  }

  if ((type === "source" || type === "citation") && message) {
    if (data?.type === "code_execution") {
      if (!message.code_executions) message.code_executions = [];

      const existingIndex = message.code_executions.findIndex(
        (execution) => execution.id === data.id,
      );
      if (existingIndex !== -1) {
        message.code_executions[existingIndex] = data;
      } else {
        message.code_executions.push(data);
      }
      message.code_executions = message.code_executions;
    } else if (message.sources) {
      message.sources.push(data);
    } else {
      message.sources = [data];
    }
    return "message-updated";
  }

  if (type === "notification") {
    const toastType = data?.type ?? "info";
    const content = data?.content ?? "";
    if (toastType === "success") notifications.success(content);
    else if (toastType === "error") notifications.error(content);
    else if (toastType === "warning") notifications.warning(content);
    else notifications.info(content);
    return "message-updated";
  }

  return null;
};
