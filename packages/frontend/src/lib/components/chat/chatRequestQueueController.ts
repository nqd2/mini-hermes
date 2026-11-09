export type ChatQueuedRequest<TFile> = {
  id: string;
  prompt: string;
  files: TFile[];
  error?: string;
};

export type ChatRequestQueueControllerDependencies<
  TFile extends { status?: string; type?: string },
> = {
  getQueue: (chatId: string) => readonly ChatQueuedRequest<TFile>[];
  getCurrentChatId: () => string;
  getLastMessage: () => { role?: string; done?: boolean } | null;
  isEmbedded: () => boolean;
  updateQueues: (
    update: (
      queues: Record<string, ChatQueuedRequest<TFile>[]>,
    ) => Record<string, ChatQueuedRequest<TFile>[]>,
  ) => void;
  submit: (prompt: string, files: TFile[]) => Promise<unknown>;
  stopResponse: (processQueue: boolean) => Promise<unknown>;
  waitForRender: () => Promise<unknown>;
  setInputFiles: (files: TFile[]) => void;
  setInputText: (text: string) => void;
  showSendError: (error: unknown) => void;
  logProcessingError: (error: unknown) => void;
};

/** Owns queued chat request ordering, recovery, and composer handoff. */
export const createChatRequestQueueController = <
  TFile extends { status?: string; type?: string },
>(
  dependencies: ChatRequestQueueControllerDependencies<TFile>,
) => {
  const processingChats = new Set<string>();

  const processNext = async (targetChatId: string) => {
    if (
      processingChats.has(targetChatId) ||
      targetChatId !== dependencies.getCurrentChatId()
    ) {
      return;
    }

    const queue = dependencies.getQueue(targetChatId);
    if (!queue.length || queue[0].error) return;

    const lastMessage = dependencies.getLastMessage();
    if (
      (lastMessage?.role === "assistant" && !lastMessage.done) ||
      queue.some((message) =>
        (message.files ?? []).some((file) =>
          ["uploading", "error"].includes(file.status ?? ""),
        ),
      )
    ) {
      return;
    }

    processingChats.add(targetChatId);
    const boundary = queue.findIndex((entry) =>
      entry.files.some((file) => file.type === "filesystem"),
    );
    const queuedMessages =
      dependencies.isEmbedded() || boundary === 0
        ? queue.slice(0, 1)
        : boundary < 0
          ? [...queue]
          : queue.slice(0, boundary);
    const queuedMessageIds = new Set(
      queuedMessages.map((message) => message.id),
    );

    try {
      const combinedPrompt = queuedMessages
        .map((message) => message.prompt)
        .join("\n\n");
      const combinedFiles = queuedMessages.flatMap((message) => message.files);

      dependencies.updateQueues((queues) => ({
        ...queues,
        [targetChatId]: (queues[targetChatId] ?? []).filter(
          (message) => !queuedMessageIds.has(message.id),
        ),
      }));

      await dependencies.submit(combinedPrompt, combinedFiles);
    } catch (error) {
      dependencies.logProcessingError(error);
      dependencies.updateQueues((queues) => ({
        ...queues,
        [targetChatId]: [
          ...queuedMessages.map((message) => ({
            ...message,
            error: String(error),
          })),
          ...(queues[targetChatId] ?? []),
        ],
      }));
    } finally {
      processingChats.delete(targetChatId);
    }
  };

  const sendNow = async (id: string) => {
    const targetChatId = dependencies.getCurrentChatId();
    if (processingChats.has(targetChatId)) return;

    const queue = dependencies.getQueue(targetChatId);
    const item = queue.find((message) => message.id === id);
    if (
      !item ||
      (item.files ?? []).some((file) =>
        ["uploading", "error"].includes(file.status ?? ""),
      )
    ) {
      return;
    }

    processingChats.add(targetChatId);
    try {
      dependencies.updateQueues((queues) => ({
        ...queues,
        [targetChatId]: queue.filter((message) => message.id !== id),
      }));
      await dependencies.stopResponse(false);
      await dependencies.waitForRender();
      await dependencies.submit(item.prompt, item.files);
    } catch (error) {
      dependencies.showSendError(error);
      dependencies.updateQueues((queues) => ({
        ...queues,
        [targetChatId]: [
          { ...item, error: String(error) },
          ...(queues[targetChatId] ?? []),
        ],
      }));
    } finally {
      processingChats.delete(targetChatId);
      // Completion may arrive before submit resolves, while the queue is locked.
      if (dependencies.getCurrentChatId() === targetChatId) {
        await processNext(targetChatId);
      }
    }
  };

  const edit = async (id: string) => {
    const chatId = dependencies.getCurrentChatId();
    const queue = dependencies.getQueue(chatId);
    const item = queue.find((message) => message.id === id);
    if (!item) return;

    dependencies.updateQueues((queues) => ({
      ...queues,
      [chatId]: queue.filter((message) => message.id !== id),
    }));
    dependencies.setInputFiles(item.files);
    dependencies.setInputText(item.prompt);

    await processNext(chatId);
  };

  const remove = async (id: string) => {
    const chatId = dependencies.getCurrentChatId();
    const queue = dependencies.getQueue(chatId);
    dependencies.updateQueues((queues) => ({
      ...queues,
      [chatId]: queue.filter((message) => message.id !== id),
    }));

    await processNext(chatId);
  };

  return { processNext, sendNow, edit, remove };
};
