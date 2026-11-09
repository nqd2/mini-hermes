export type ChatHistoryMessage = Record<string, any>;
export type ChatHistorySnapshot = {
  messages: Record<string, ChatHistoryMessage>;
  currentId: string | null;
  [key: string]: any;
};

export type ChatHistoryMergeOptions = {
  generating: boolean;
  taskIds: readonly string[] | null | undefined;
  currentUserId: string | null | undefined;
  fallbackOwnerId: string | null | undefined;
};

export type ChatHistoryMergeResult = {
  history: ChatHistorySnapshot;
  followedIncoming: boolean;
};

/** Merges server history without replacing locally changed messages or switching active branches. */
export const mergeChatHistoryMessages = (
  history: ChatHistorySnapshot,
  incoming: Partial<ChatHistorySnapshot> | null | undefined,
  options: ChatHistoryMergeOptions,
  baseline: Record<string, ChatHistoryMessage> | null = null,
): ChatHistoryMergeResult => {
  const currentId = history.currentId;
  const next = incoming?.messages ?? {};
  const messages = { ...history.messages };
  const hasNewBranch = Object.values(next).some(
    (message) =>
      message.role === "user" &&
      !history.messages[message.id] &&
      Object.values(history.messages).some(
        (existing) =>
          existing.role === "user" && existing.parentId === message.parentId,
      ),
  );
  const hasOwnedActiveTask = Boolean(
    options.taskIds?.length &&
    Object.values(history.messages).some(
      (message) =>
        message.role === "assistant" &&
        message.done === false &&
        (message.user_id ?? options.fallbackOwnerId) === options.currentUserId,
    ),
  );
  const shouldFollow =
    !hasNewBranch &&
    !options.generating &&
    !hasOwnedActiveTask &&
    Object.keys(next).some((id) => !history.messages[id]);

  for (const [id, message] of Object.entries(next)) {
    const local = messages[id];
    const ownActive =
      local?.done === false &&
      (local.user_id ?? options.fallbackOwnerId) === options.currentUserId &&
      (Boolean(options.taskIds?.length) || options.generating);
    if (!local || (!ownActive && (!baseline || baseline[id] === local))) {
      messages[id] = { ...local, ...message };
      if (
        local?.done === true &&
        message.done === false &&
        local.meta?.task_id &&
        local.meta.task_id === message.meta?.task_id &&
        !options.taskIds?.includes(local.meta.task_id)
      ) {
        messages[id].done = true;
      }
    }
    if (local) {
      messages[id] = {
        ...messages[id],
        childrenIds: [
          ...new Set([
            ...(local.childrenIds ?? []),
            ...(message.childrenIds ?? []),
          ]),
        ],
      };
    }
  }

  const followedIncoming = Boolean(
    (!currentId || shouldFollow) &&
    incoming?.currentId &&
    next[incoming.currentId],
  );
  return {
    history: {
      ...history,
      messages,
      currentId: followedIncoming ? incoming!.currentId! : currentId,
    },
    followedIncoming,
  };
};
