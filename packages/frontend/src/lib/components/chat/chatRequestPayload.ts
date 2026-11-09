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

export type ChatRequestFeatureDependencies = {
  config?: Record<string, any> | null;
  user?: Record<string, any> | null;
  showCallOverlay: boolean;
  callMode: string;
  bridgeConnected: boolean;
  imageGenerationEnabled: boolean;
  codeInterpreterEnabled: boolean;
  webSearchActive: boolean;
  settingsMemory?: boolean | null;
};

/** Resolves feature flags using the same permission and settings precedence as Chat. */
export const buildChatRequestFeatures = ({
  config,
  user,
  showCallOverlay,
  callMode,
  bridgeConnected,
  imageGenerationEnabled,
  codeInterpreterEnabled,
  webSearchActive,
  settingsMemory,
}: ChatRequestFeatureDependencies): Record<string, boolean> => {
  let features: Record<string, boolean> = {};

  if (config?.features) {
    features = {
      voice: (showCallOverlay && callMode === 'current') || bridgeConnected,
      image_generation:
        config.features.enable_image_generation &&
        (user?.role === 'admin' || user?.permissions?.features?.image_generation)
          ? imageGenerationEnabled
          : false,
      code_interpreter:
        config.features.enable_code_interpreter &&
        (user?.role === 'admin' || user?.permissions?.features?.code_interpreter)
          ? codeInterpreterEnabled
          : false,
      web_search: webSearchActive,
    };
  }

  if (settingsMemory ?? config?.features?.enable_memories ?? false) {
    features = { ...features, memory: true };
  }

  return features;
};

/** Normalizes configured stop tokens while retaining malformed values verbatim. */
export const normalizeStopTokens = (
  stop?: string | string[] | null,
): string[] | undefined => {
  if (!stop) return undefined;

  const tokens = Array.isArray(stop) ? stop : stop.split(',').map((token) => token.trim());
  return tokens.filter(Boolean).map((token) => {
    try {
      return decodeURIComponent(JSON.parse(`"${token.replace(/"/g, '\\"')}"`));
    } catch {
      return token;
    }
  });
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
