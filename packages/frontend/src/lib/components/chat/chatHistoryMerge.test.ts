import { describe, expect, it } from "vitest";
import { mergeChatHistoryMessages } from "./chatHistoryMerge";

const merge = (
  history: any,
  incoming: any,
  options: Record<string, unknown> = {},
  baseline: Record<string, any> | null = null,
) =>
  mergeChatHistoryMessages(
    history,
    incoming,
    {
      generating: false,
      taskIds: null,
      currentUserId: "user-1",
      fallbackOwnerId: "user-1",
      ...options,
    },
    baseline,
  );

describe("mergeChatHistoryMessages", () => {
  it("merges incoming fields, preserves local edits after the baseline and unions child IDs", () => {
    const localMessage = {
      id: "message-1",
      content: "local newer content",
      childrenIds: ["child-1", "child-2"],
    };
    const history = {
      currentId: "message-1",
      messages: { "message-1": localMessage },
    };
    const baseline = {
      "message-1": { ...localMessage, content: "before request" },
    };
    const incoming = {
      currentId: "message-1",
      messages: {
        "message-1": {
          id: "message-1",
          content: "stale server text",
          childrenIds: ["child-2", "child-3"],
        },
      },
    };

    const result = merge(history, incoming, {}, baseline);

    expect(result.history.messages["message-1"]).toMatchObject({
      content: "local newer content",
      childrenIds: ["child-1", "child-2", "child-3"],
    });
    expect(result.history).not.toBe(history);
    expect(result.history.messages).not.toBe(history.messages);
  });

  it("keeps the selected branch when the server returns a new sibling prompt", () => {
    const history = {
      currentId: "assistant-current",
      messages: {
        "user-current": { id: "user-current", role: "user", parentId: "root" },
        "assistant-current": {
          id: "assistant-current",
          role: "assistant",
          parentId: "user-current",
        },
      },
    };
    const incoming = {
      currentId: "assistant-new",
      messages: {
        "user-new": { id: "user-new", role: "user", parentId: "root" },
        "assistant-new": {
          id: "assistant-new",
          role: "assistant",
          parentId: "user-new",
        },
      },
    };

    const result = merge(history, incoming);

    expect(result.history.currentId).toBe("assistant-current");
    expect(result.followedIncoming).toBe(false);
  });

  it("follows a new server branch when the user is idle", () => {
    const history = {
      currentId: "parent",
      messages: { parent: { id: "parent" } },
    };
    const incoming = {
      currentId: "assistant-new",
      messages: { "assistant-new": { id: "assistant-new", role: "assistant" } },
    };

    const result = merge(history, incoming);

    expect(result.history.currentId).toBe("assistant-new");
    expect(result.followedIncoming).toBe(true);
  });

  it("does not follow new messages while generating or while an owned task is active", () => {
    const history = {
      currentId: "parent",
      messages: { parent: { id: "parent" } },
    };
    const incoming = {
      currentId: "assistant-new",
      messages: { "assistant-new": { id: "assistant-new", role: "assistant" } },
    };

    expect(
      merge(history, incoming, { generating: true }).history.currentId,
    ).toBe("parent");
    expect(
      merge(
        {
          ...history,
          messages: { "assistant-active": { role: "assistant", done: false } },
        },
        incoming,
        { taskIds: ["task-1"] },
      ).history.currentId,
    ).toBe("parent");
  });

  it("keeps a recovered response done when an inactive matching task appears unfinished", () => {
    const history = {
      currentId: "assistant-1",
      messages: {
        "assistant-1": {
          id: "assistant-1",
          role: "assistant",
          done: true,
          meta: { task_id: "task-1" },
          content: "recovered answer",
        },
      },
    };
    const incoming = {
      currentId: "assistant-1",
      messages: {
        "assistant-1": {
          id: "assistant-1",
          role: "assistant",
          done: false,
          meta: { task_id: "task-1" },
          content: "stale partial answer",
        },
      },
    };

    const result = merge(history, incoming);

    expect(result.history.messages["assistant-1"]).toMatchObject({
      done: true,
      content: "stale partial answer",
    });
  });

  it("does not block on an active task owned by another user", () => {
    const history = {
      currentId: "parent",
      messages: {
        parent: { id: "parent" },
        "assistant-active": {
          role: "assistant",
          user_id: "owner-2",
          done: false,
        },
      },
    };
    const incoming = {
      currentId: "assistant-new",
      messages: { "assistant-new": { id: "assistant-new", role: "assistant" } },
    };

    const result = merge(history, incoming, {
      taskIds: ["task-1"],
      currentUserId: "user-1",
      fallbackOwnerId: "owner-2",
    });

    expect(result.history.currentId).toBe("assistant-new");
  });
});
