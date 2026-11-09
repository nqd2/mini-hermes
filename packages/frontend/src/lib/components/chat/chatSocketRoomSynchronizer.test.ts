import { describe, expect, it, vi } from "vitest";
import { syncChatRoom } from "./chatSocketRoomSynchronizer";

const makeHarness = () => {
  let joinedChatId = "";
  let activeChatId = "chat-1";
  let joinCallback: ((accepted: boolean) => void) | undefined;
  const socket = {
    connected: true,
    id: "socket-1",
    emit: vi.fn(
      (
        _event: string,
        payload: any,
        callback?: (accepted: boolean) => void,
      ) => {
        if (payload.data.type === "join") joinCallback = callback;
      },
    ),
  };
  const dependencies = {
    getJoinedChatId: () => joinedChatId,
    setJoinedChatId: vi.fn((id: string) => {
      joinedChatId = id;
    }),
    getCurrentChatId: () => activeChatId,
    isTemporaryChatId: (id: string | null | undefined) =>
      id?.startsWith("temp-") ?? false,
    clearTyping: vi.fn(),
    clearPresence: vi.fn(),
    refreshChat: vi.fn(),
  };

  return {
    socket,
    dependencies,
    getJoinedChatId: () => joinedChatId,
    setActiveChatId: (id: string) => {
      activeChatId = id;
    },
    acknowledgeJoin: (accepted: boolean) => joinCallback?.(accepted),
  };
};

describe("syncChatRoom", () => {
  it("joins an eligible chat and refreshes only after the current socket accepts", () => {
    const harness = makeHarness();

    syncChatRoom(harness.socket, "chat-1", false, harness.dependencies);

    expect(harness.dependencies.setJoinedChatId).toHaveBeenCalledWith("chat-1");
    expect(harness.getJoinedChatId()).toBe("chat-1");
    expect(harness.socket.emit).toHaveBeenCalledWith(
      "events:chat",
      { chat_id: "chat-1", data: { type: "join" } },
      expect.any(Function),
    );
    harness.acknowledgeJoin(true);
    expect(harness.dependencies.refreshChat).toHaveBeenCalledOnce();
  });

  it("leaves the previous room before joining the next room", () => {
    const harness = makeHarness();
    harness.dependencies.setJoinedChatId("chat-old");

    syncChatRoom(harness.socket, "chat-1", false, harness.dependencies);

    expect(
      harness.socket.emit.mock.calls.map(([, payload]) => payload.data.type),
    ).toEqual(["leave", "join"]);
    expect(harness.dependencies.clearTyping).toHaveBeenCalledOnce();
    expect(harness.dependencies.clearPresence).toHaveBeenCalledOnce();
    expect(harness.getJoinedChatId()).toBe("chat-1");
  });

  it("leaves an existing room without joining temporary or disconnected chats", () => {
    const harness = makeHarness();
    harness.dependencies.setJoinedChatId("chat-old");

    syncChatRoom(harness.socket, "temp-1", false, harness.dependencies);

    expect(
      harness.socket.emit.mock.calls.map(([, payload]) => payload.data.type),
    ).toEqual(["leave"]);
    expect(harness.getJoinedChatId()).toBe("");

    harness.socket.connected = false;
    syncChatRoom(harness.socket, "chat-2", false, harness.dependencies);
    expect(harness.socket.emit).toHaveBeenCalledTimes(1);
    expect(harness.getJoinedChatId()).toBe("");
  });

  it("ignores stale join acknowledgements and clears presence when access is refused", () => {
    const harness = makeHarness();

    syncChatRoom(harness.socket, "chat-1", false, harness.dependencies);
    harness.setActiveChatId("chat-2");
    harness.dependencies.clearPresence.mockClear();
    harness.acknowledgeJoin(false);
    expect(harness.dependencies.refreshChat).not.toHaveBeenCalled();
    expect(harness.dependencies.clearPresence).not.toHaveBeenCalled();

    harness.setActiveChatId("chat-1");
    syncChatRoom(harness.socket, "chat-1", false, harness.dependencies);
    harness.dependencies.clearPresence.mockClear();
    harness.acknowledgeJoin(false);
    expect(harness.dependencies.clearPresence).toHaveBeenCalledOnce();
    expect(harness.dependencies.refreshChat).toHaveBeenCalledOnce();
  });

  it("does not emit another join when the chat room is already current", () => {
    const harness = makeHarness();
    harness.dependencies.setJoinedChatId("chat-1");

    syncChatRoom(harness.socket, "chat-1", false, harness.dependencies);

    expect(harness.socket.emit).not.toHaveBeenCalled();
    expect(harness.dependencies.clearTyping).not.toHaveBeenCalled();
    expect(harness.dependencies.clearPresence).not.toHaveBeenCalled();
  });
});
