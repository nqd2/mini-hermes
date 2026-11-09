import { describe, expect, it } from "vitest";
import { insertSubmittedUserMessage } from "./chatMessageSubmission";

const makeHistory = () => ({
  currentId: "parent-1",
  messages: {
    "parent-1": { id: "parent-1", childrenIds: [] as string[] },
  },
});

describe("insertSubmittedUserMessage", () => {
  it("adds a new user message under the current history node", () => {
    const history = makeHistory();

    const message = insertSubmittedUserMessage(history, {
      id: "user-1",
      content: "Please analyze this file",
      files: [{ id: "file-1" }],
      timestamp: 1700000000,
      models: ["model-1"],
      userId: "user-1",
      userName: "Ada",
    });

    expect(message).toEqual({
      id: "user-1",
      parentId: "parent-1",
      childrenIds: [],
      role: "user",
      user: { id: "user-1", name: "Ada" },
      user_id: "user-1",
      content: "Please analyze this file",
      files: [{ id: "file-1" }],
      timestamp: 1700000000,
      models: ["model-1"],
    });
    expect(history.messages["parent-1"].childrenIds).toEqual(["user-1"]);
    expect(history.currentId).toBe("user-1");
  });

  it("preserves an existing bridge message and does not duplicate the parent edge", () => {
    const history = makeHistory();
    history.messages["bridge-user"] = {
      id: "bridge-user",
      parentId: "prior-parent",
      childrenIds: ["assistant-1"],
      role: "user",
      content: "transcribed text",
      meta: { source: "bridge" },
    };

    const message = insertSubmittedUserMessage(history, {
      id: "bridge-user",
      content: "replacement content is ignored",
      files: [{ id: "file-2" }],
      timestamp: 1800000000,
      models: ["model-2"],
      userId: "user-1",
      userName: "Ada",
    });

    expect(message).toMatchObject({
      id: "bridge-user",
      parentId: "prior-parent",
      childrenIds: ["assistant-1"],
      role: "user",
      content: "transcribed text",
      meta: { source: "bridge" },
      files: [{ id: "file-2" }],
    });
    expect(history.messages["parent-1"].childrenIds).toEqual([]);
    expect(history.currentId).toBe("bridge-user");
  });

  it("leaves files undefined for messages without file attachments", () => {
    const history = { currentId: null, messages: {} };

    const message = insertSubmittedUserMessage(history, {
      id: "root-user",
      content: "Hello",
      files: [],
      timestamp: 1700000000,
      models: ["model-1"],
    });

    expect(message.files).toBeUndefined();
    expect(message.parentId).toBeNull();
    expect(history.currentId).toBe("root-user");
  });
});
