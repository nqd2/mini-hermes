type ChatMessage = Record<string, any>;

type ChatTaskCancellationOptions = {
  chatId: string;
  messageId: string;
  shared: boolean;
  currentMessageId: () => string | null;
  message: ChatMessage;
  output?: unknown;
  resolvePendingInteractions: (messageId: string) => void;
  cancelBridge: (messageId: string) => void;
  clearContextCompaction: (messageId: string) => void;
  getParentChildren: (
    parentId: string | null | undefined,
  ) => string[] | null | undefined;
  markMessageDone: (messageId: string) => void;
  setTaskIds: (taskIds: null) => void;
  processNextInQueue: (chatId: string) => Promise<unknown>;
};

/** Applies cancellation effects in one order shared by bridge and ordinary chat tasks. */
export const handleChatTaskCancellation = async (
  options: ChatTaskCancellationOptions,
): Promise<void> => {
  options.resolvePendingInteractions(options.messageId);
  options.message.bridgeCancelled = true;
  options.cancelBridge(options.messageId);
  options.clearContextCompaction(options.messageId);
  if (options.output) options.message.output = options.output;

  if (!options.shared && options.messageId === options.currentMessageId()) {
    options.setTaskIds(null);
    for (const childMessageId of options.getParentChildren(
      options.message.parentId,
    ) ?? []) {
      options.markMessageDone(childMessageId);
      options.clearContextCompaction(childMessageId);
    }
    options.message.done = true;
    await options.processNextInQueue(options.chatId);
  } else {
    options.message.done = true;
  }
};

type ChatMessageErrorOptions = {
  chatId: string;
  messageId: string;
  shared: boolean;
  error: unknown;
  message: ChatMessage;
  currentMessageId: () => string | null;
  handleOpenAIError: (error: unknown, message: ChatMessage) => void;
  clearContextCompaction: (messageId: string) => void;
  processNextInQueue: (chatId: string) => Promise<unknown>;
};

/** Applies shared/local error state and only resumes the queue for unfinished local responses. */
export const handleChatMessageError = async (
  options: ChatMessageErrorOptions,
): Promise<void> => {
  const responseCompleted = options.message.done;
  if (options.shared) {
    options.message.error = options.error;
    options.message.done = true;
  } else {
    options.handleOpenAIError(options.error, options.message);
  }

  if (!options.shared && !responseCompleted) {
    options.clearContextCompaction(options.message.id);
    if (options.messageId === options.currentMessageId()) {
      await options.processNextInQueue(options.chatId);
    }
  }
};
