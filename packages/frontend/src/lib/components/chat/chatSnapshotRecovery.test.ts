import { describe, expect, it } from "vitest";
import { recoverChatSnapshot } from "./chatSnapshotRecovery";
import type { ChatHistorySnapshot } from "./chatHistoryMerge";

const setup = () => {
  const history: ChatHistorySnapshot = {
    currentId: "reply",
    messages: {
      reply: {
        id: "reply",
        role: "assistant",
        user_id: "owner",
        done: false,
        content: "",
        output: [],
        childrenIds: [],
        meta: { task_id: "run-1" },
      },
    },
  };
  const baseline = { ...history.messages };
  const incoming: ChatHistorySnapshot = {
    currentId: "reply",
    messages: {
      reply: {
        ...history.messages.reply,
        done: true,
        content: "Saved complete answer",
        output: [
          {
            type: "message",
            content: [{ type: "output_text", text: "Saved complete answer" }],
          },
        ],
      },
    },
  };
  const options = {
    generating: false,
    taskIds: ["run-1"],
    currentUserId: "owner",
    fallbackOwnerId: "owner",
    recoverActive: true,
  };
  return {
    history,
    incoming,
    baseline,
    options,
    recover: (tasks: string[] | null = ["run-1"]) =>
      recoverChatSnapshot(history, incoming, baseline, options, tasks),
  };
};

describe("recoverChatSnapshot", () => {
  it("recovers a missed completion while the same task still generates title/follow-ups", () => {
    const h = setup();
    const result = h.recover();
    expect(result.history.messages.reply).toMatchObject(
      h.incoming.messages.reply,
    );
    expect(result.taskIds).toBeNull();
    expect(result.processQueue).toBe(true);
    expect(h.history.messages.reply.done).toBe(false);
  });

  it("recovers the current stream snapshot while the response is still running", () => {
    const h = setup();
    h.incoming.messages.reply.done = false;
    h.incoming.messages.reply.content =
      "Partial output received during disconnect";
    const result = h.recover();
    expect(result.history.messages.reply).toMatchObject(
      h.incoming.messages.reply,
    );
    expect(result.taskIds).toEqual(["run-1"]);
    expect(result.processQueue).toBe(false);
  });

  it("recovers a blank placeholder that missed the initial task metadata event", () => {
    const h = setup();
    delete h.history.messages.reply.meta;
    const result = h.recover();
    expect(result.history.messages.reply.content).toBe("Saved complete answer");
    expect(result.history.messages.reply.meta.task_id).toBe("run-1");
    expect(result.taskIds).toBeNull();
  });

  it("settles an interrupted response when the server has no tasks after restart", () => {
    const h = setup();
    h.incoming.messages.reply.done = false;
    const result = h.recover([]);
    expect(result.history.messages.reply.done).toBe(true);
    expect(result.taskIds).toBeNull();
    expect(result.processQueue).toBe(true);
    expect(h.incoming.messages.reply.done).toBe(false);
  });

  it("does not treat a failed task lookup as an empty task list", () => {
    const h = setup();
    h.incoming.messages.reply.done = false;
    const result = h.recover(null);
    expect(result.history.messages.reply.done).toBe(false);
    expect(result.taskIds).toBeUndefined();
    expect(result.processQueue).toBe(false);
  });

  it.each([{ tasks: [] }, { tasks: ["run-1"] }])(
    "preserves live updates received during refresh with tasks $tasks",
    ({ tasks }) => {
      const h = setup();
      h.history.messages.reply = {
        ...h.history.messages.reply,
        content: "New live stream",
      };
      const result = h.recover(tasks);
      expect(result.history.messages.reply.content).toBe("New live stream");
      expect(result.history.messages.reply.done).toBe(false);
      expect(result.taskIds).toBeUndefined();
      expect(result.processQueue).toBe(false);
    },
  );

  it("does not overwrite a newer run with a snapshot of the previous run", () => {
    const h = setup();
    h.incoming.messages.reply.meta = { task_id: "old-run" };
    const result = h.recover([]);
    expect(result.history.messages.reply.done).toBe(false);
    expect(result.history.messages.reply.content).toBe("");
    expect(result.taskIds).toBeUndefined();
    expect(result.processQueue).toBe(false);
  });

  it.each(["generating", "tasks-changed"])(
    "keeps active state when %s during refresh",
    (change) => {
      const h = setup();
      if (change === "generating") h.options.generating = true;
      else h.options.recoverActive = false;
      const result = h.recover([]);
      expect(result.history.messages.reply.done).toBe(false);
      expect(result.taskIds).toBeUndefined();
      expect(result.processQueue).toBe(false);
    },
  );

  it.each(["ask_user", "shell"])(
    "preserves pending %s interaction after task exit",
    (name) => {
      const h = setup();
      h.incoming.messages.reply.done = false;
      h.incoming.messages.reply.output = [
        { type: "function_call", name, call_id: "call-1", status: "pending" },
      ];
      const result = h.recover([]);
      expect(result.history.messages.reply.done).toBe(false);
      expect(result.taskIds).toBeNull();
      expect(result.processQueue).toBe(false);
    },
  );

  it("keeps tasks active until all model responses finish", () => {
    const h = setup();
    const second = {
      ...h.history.messages.reply,
      id: "second",
      meta: { task_id: "run-2" },
    };
    h.history.messages.second = second;
    h.baseline.second = second;
    h.incoming.messages.second = { ...second, content: "Still streaming" };
    const result = h.recover(["run-1", "run-2"]);
    expect(result.history.messages.reply.done).toBe(true);
    expect(result.history.messages.second.done).toBe(false);
    expect(result.taskIds).toEqual(["run-1", "run-2"]);
    expect(result.processQueue).toBe(false);
  });

  it("does not finalize another user's response or switch the selected branch", () => {
    const h = setup();
    h.history.messages.other = {
      id: "other",
      role: "assistant",
      user_id: "other-user",
      done: false,
    };
    h.baseline.other = h.history.messages.other;
    h.incoming.currentId = "other";
    h.incoming.messages.other = {
      ...h.history.messages.other,
      content: "Their stream",
    };
    const result = h.recover([]);
    expect(result.history.currentId).toBe("reply");
    expect(result.history.messages.other.done).toBe(false);
  });
});
