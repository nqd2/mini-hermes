export type SubmittedMessageHistory = {
  currentId: string | null;
  messages: Record<string, any>;
};

export type SubmittedUserMessageInput = {
  id: string;
  content: string;
  files: any[];
  timestamp: number;
  models: string[];
  userId?: string | null;
  userName?: string | null;
};

/** Adds a submitted user message while preserving pre-existing bridge messages. */
export const insertSubmittedUserMessage = <
  THistory extends SubmittedMessageHistory,
>(
  history: THistory,
  input: SubmittedUserMessageInput,
) => {
  const userMessage = {
    id: input.id,
    parentId: history.currentId ?? null,
    childrenIds: [],
    role: "user",
    user: { id: input.userId, name: input.userName },
    user_id: input.userId,
    content: input.content,
    files: input.files.length > 0 ? input.files : undefined,
    timestamp: input.timestamp,
    models: input.models,
  };

  const existingUserMessage = history.messages[input.id];
  const message = existingUserMessage
    ? { ...existingUserMessage, files: userMessage.files }
    : userMessage;
  history.messages[input.id] = message;

  if (!existingUserMessage && history.currentId !== null) {
    history.messages[history.currentId].childrenIds.push(input.id);
  }

  history.currentId = input.id;
  return message;
};
