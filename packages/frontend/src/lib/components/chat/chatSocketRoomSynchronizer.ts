export type ChatRoomSocket = {
  connected?: boolean;
  id?: string;
  emit: (...args: any[]) => unknown;
};

export type ChatRoomSyncDependencies = {
  getJoinedChatId: () => string;
  setJoinedChatId: (chatId: string) => void;
  getCurrentChatId: () => string | null | undefined;
  isTemporaryChatId: (chatId: string | null | undefined) => boolean;
  clearTyping: () => void;
  clearPresence: () => void;
  refreshChat: () => unknown;
};

export const syncChatRoom = (
  socket: ChatRoomSocket | null | undefined,
  chatId: string,
  temporary: boolean,
  dependencies: ChatRoomSyncDependencies,
): void => {
  const next =
    socket?.connected &&
    chatId &&
    !temporary &&
    !dependencies.isTemporaryChatId(chatId)
      ? chatId
      : "";
  const joinedChatId = dependencies.getJoinedChatId();
  if (next === joinedChatId) return;

  dependencies.clearTyping();
  dependencies.clearPresence();
  if (joinedChatId && socket?.connected) {
    socket.emit("events:chat", {
      chat_id: joinedChatId,
      data: { type: "leave" },
    });
  }

  dependencies.setJoinedChatId(next);
  const connectionId = socket?.id;
  if (next && socket) {
    socket.emit(
      "events:chat",
      { chat_id: next, data: { type: "join" } },
      (accepted: boolean) => {
        if (
          !socket.connected ||
          socket.id !== connectionId ||
          dependencies.getCurrentChatId() !== next
        ) {
          return;
        }
        if (!accepted) dependencies.clearPresence();
        // A refused join may indicate lost access; refresh so the reader can leave the chat.
        void dependencies.refreshChat();
      },
    );
  }
};
