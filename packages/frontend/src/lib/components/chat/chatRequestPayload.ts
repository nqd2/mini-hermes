export type ChatRequestPayloadDependencies = {
  stream: boolean;
  modelId: string;
  messages: any[];
  settingsParams?: Record<string, any>;
  modelParams: Record<string, any>;
  stop?: string[];
  files?: any[] | null;
  embedded: boolean;
  noteFiles?: any[];
  toolRouting: {
    filterIds?: string[];
    toolIds: string[];
    skillIds?: string[];
    terminalId?: string;
    toolServers: any[];
  };
  features: Record<string, any>;
  variables: Record<string, any>;
  includeChatVariables: boolean;
  chatVariables?: Record<string, any>;
  modelItem?: any;
  sessionId?: string;
  chatId?: string | null;
  folderId?: string;
  responseMessageId: string;
  messageIdsList?: Array<{ model_id: string; message_id: string }>;
  userMessage: { parentId?: string | null; [key: string]: any };
  regenerationPrompt?: string | null;
  regenerationMessageId?: string | null;
  continueResponse: boolean;
  backgroundTasks: Record<string, any>;
};

/** Assembles the existing OpenAI-compatible chat completion request shape. */
export const buildChatRequestPayload = ({
  stream,
  modelId,
  messages,
  settingsParams,
  modelParams,
  stop,
  files,
  embedded,
  noteFiles,
  toolRouting,
  features,
  variables,
  includeChatVariables,
  chatVariables,
  modelItem,
  sessionId,
  chatId,
  folderId,
  responseMessageId,
  messageIdsList,
  userMessage,
  regenerationPrompt,
  regenerationMessageId,
  continueResponse,
  backgroundTasks,
}: ChatRequestPayloadDependencies) => ({
  stream,
  model: modelId,
  ...(messages.length > 0 ? { messages } : {}),
  params: {
    ...settingsParams,
    ...Object.fromEntries(
      Object.entries(modelParams).filter(([, value]) => value !== null),
    ),
    model_controls: settingsParams?.model_controls ?? {},
    stop,
  },
  files: embedded
    ? [...(files ?? []), ...(noteFiles ?? [])]
    : (files?.length ?? 0) > 0
      ? files
      : undefined,
  filter_ids: toolRouting.filterIds,
  tool_ids: toolRouting.toolIds,
  skill_ids: toolRouting.skillIds,
  terminal_id: toolRouting.terminalId,
  tool_servers: toolRouting.toolServers,
  features,
  variables: { ...variables },
  ...(includeChatVariables ? { chat_variables: chatVariables } : {}),
  model_item: modelItem,
  session_id: sessionId,
  chat_id: chatId || undefined,
  folder_id: folderId,
  id: responseMessageId,
  ...(messageIdsList ? { message_ids: messageIdsList } : {}),
  parent_id: userMessage.parentId ?? null,
  user_message: userMessage,
  ...(regenerationPrompt ? { regeneration_prompt: regenerationPrompt } : {}),
  ...(regenerationMessageId
    ? { regeneration_message_id: regenerationMessageId }
    : {}),
  ...(continueResponse ? { assistant_message_id: responseMessageId } : {}),
  background_tasks: backgroundTasks,
});
