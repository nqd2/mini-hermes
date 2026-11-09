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
