export type ChatCompletionResponseDependencies = {
  response: any;
  responseMessageId: string;
  responseMessage: Record<string, any>;
  messageIdsList?: Array<{ model_id: string; message_id: string }>;
  messages: Record<string, any>;
  onError: (error: any, responseMessage: Record<string, any>) => void | Promise<void>;
  onToolCallResolved: (response: any) => void | Promise<void>;
};

export type ChatCompletionResponseOutcome = 'empty' | 'error' | 'success';

export type ChatSyncFallbackOptions = {
  response: any;
  responseOutcome: ChatCompletionResponseOutcome;
  hasSocketSession: boolean;
  responseMessageId: string;
  messageIdsList?: Array<{ model_id: string; message_id: string }>;
  messages: Record<string, any>;
};

/** Returns true when a completion finished synchronously without a live WebSocket stream and needs an HTTP reload. */
export const shouldReloadChatAfterCompletion = ({
  response,
  responseOutcome,
  hasSocketSession,
  responseMessageId,
  messageIdsList,
  messages,
}: ChatSyncFallbackOptions): boolean => {
  if (responseOutcome === 'error') return false;
  const trackedMessageIds = messageIdsList ?? [{ message_id: responseMessageId }];
  const hasPendingTracked = trackedMessageIds.some(
    ({ message_id }) => messages[message_id]?.done === false,
  );
  if (!hasPendingTracked) return false;
  if (response?.sync_fallback === true) return true;
  const hasTasks = Boolean(response?.task_ids?.length || response?.task_id);
  return !hasSocketSession && !hasTasks;
};

/** Routes a completion response and resolves it only while tracked messages remain pending. */
export const handleChatCompletionResponse = async ({
  response,
  responseMessageId,
  responseMessage,
  messageIdsList,
  messages,
  onError,
  onToolCallResolved,
}: ChatCompletionResponseDependencies): Promise<ChatCompletionResponseOutcome> => {
  if (!response) return 'empty';

  if (response.error) {
    await onError(response.error, responseMessage);
    return 'error';
  }

  const trackedMessageIds = messageIdsList ?? [{ message_id: responseMessageId }];
  if (trackedMessageIds.some(({ message_id }) => messages[message_id]?.done === false)) {
    await onToolCallResolved(response);
  }

  return 'success';
};
