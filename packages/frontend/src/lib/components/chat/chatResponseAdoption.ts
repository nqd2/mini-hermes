type QueuedRequest = { files: any[]; [key: string]: any };

export type ChatResponseAdoptionDependencies = {
  responseChatId?: string | null;
  requestChatId?: string | null;
  activeChatId?: string | null;
  hasResponseMessage: boolean;
  userMessage?: { files?: any[]; [key: string]: any };
  parentMessage?: { files?: any[]; [key: string]: any };
  queueStore: {
    update: (
      updater: (queue: Record<string, QueuedRequest[]>) => Record<string, QueuedRequest[]>,
    ) => void;
  };
  temporaryChatEnabled: boolean;
  embedded: boolean;
  token: string;
  params: Record<string, any>;
  setChatId: (chatId: string) => void | Promise<void>;
  getDraft: () => any;
  saveDraft: (draft: any, chatId: string, preserveSelection: boolean) => void;
  replaceUrl: (url: string) => void;
  refreshChatList: (token: string) => any;
  updateChatParams: (
    token: string,
    chatId: string,
    params: Record<string, any>,
  ) => void | Promise<void>;
  processNextInQueue: (chatId: string) => void | Promise<void>;
};

/** Adopts the server-created chat only while the originating view still owns navigation. */
export const adoptChatResponse = async ({
  responseChatId,
  requestChatId,
  activeChatId,
  hasResponseMessage,
  userMessage,
  parentMessage,
  queueStore,
  temporaryChatEnabled,
  embedded,
  token,
  params,
  setChatId,
  getDraft,
  saveDraft,
  replaceUrl,
  refreshChatList,
  updateChatParams,
  processNextInQueue,
}: ChatResponseAdoptionDependencies): Promise<boolean> => {
  if (
    !responseChatId ||
    !hasResponseMessage ||
    responseChatId === requestChatId ||
    (activeChatId !== requestChatId && activeChatId !== responseChatId)
  ) {
    return false;
  }

  queueStore.update((queue) => {
    if (!queue[requestChatId]?.length) return queue;

    const { [requestChatId as string]: pendingQueue, ...rest } = queue;
    return {
      ...rest,
      [responseChatId]: [
        ...pendingQueue.map((entry) => ({
          ...entry,
          files: entry.files.map((file) =>
            file.type === 'filesystem' && file.session_id == null
              ? { ...file, session_id: responseChatId }
              : file,
          ),
        })),
        ...(rest[responseChatId] ?? []),
      ],
    };
  });

  for (const file of userMessage?.files ?? []) {
    if (file.type === 'filesystem' && file.session_id == null) {
      file.session_id = responseChatId;
    }
  }
  for (const file of parentMessage?.files ?? []) {
    if (file.type === 'filesystem' && file.session_id == null) {
      file.session_id = responseChatId;
    }
  }

  await setChatId(responseChatId);
  if (!temporaryChatEnabled && !embedded) {
    saveDraft(getDraft(), responseChatId, false);
    replaceUrl(`/c/${responseChatId}`);
    await refreshChatList(token);
    if (Object.keys(params).length > 0) {
      await updateChatParams(token, responseChatId, params);
    }
  }
  await processNextInQueue(responseChatId);
  return true;
};
