import { describe, expect, it, vi } from "vitest";
import { reconcileLoadedChatTasks } from "./chatLoadTaskReconciliation";

const createOptions = (overrides: Record<string, unknown> = {}) => {
  const messages: Record<string, any> = {
    "active-response": {
      id: "active-response",
      role: "assistant",
      user_id: "user-1",
      done: false,
    },
    "other-user-response": {
      id: "other-user-response",
      role: "assistant",
      user_id: "user-2",
      done: false,
    },
    "ask-user-response": {
      id: "ask-user-response",
      role: "assistant",
      user_id: "user-1",
      done: false,
      output: [{ type: "function_call", name: "ask_user", status: "pending" }],
    },
  };
  const snapshotTaskIds = ["previous-task"];
  const currentTaskIds = vi.fn(() => snapshotTaskIds);
  const canReconcile = vi.fn(() => true);
  const setTaskIds = vi.fn();
  const markHistoryChanged = vi.fn();

  return {
    messages,
    currentTaskIds,
    canReconcile,
    setTaskIds,
    markHistoryChanged,
    options: {
      pendingTaskIds: [],
      currentMessage: { role: "assistant", done: false },
      snapshotTaskIds,
      getCurrentTaskIds: currentTaskIds,
      canReconcile,
      messages,
      currentUserId: "user-1",
      fallbackOwnerId: "user-1",
      messageHasPendingAskUser: (message: any) =>
        message.output?.some(
          (item: any) => item.name === "ask_user" && item.status === "pending",
        ),
      setTaskIds,
      markHistoryChanged,
      ...overrides,
    },
  };
};

describe("reconcileLoadedChatTasks", () => {
  it("keeps backend tasks when the current response is still incomplete", () => {
    const harness = createOptions({ pendingTaskIds: ["task-a", "task-b"] });

    expect(reconcileLoadedChatTasks(harness.options as any)).toBe(false);
    expect(harness.setTaskIds).toHaveBeenCalledWith(["task-a", "task-b"]);
    expect(harness.markHistoryChanged).not.toHaveBeenCalled();
  });

  it("clears task IDs when only background tasks remain after response completion", () => {
    const harness = createOptions({
      pendingTaskIds: ["follow-up-task"],
      currentMessage: { role: "assistant", done: true },
    });

    expect(reconcileLoadedChatTasks(harness.options as any)).toBe(false);
    expect(harness.setTaskIds).toHaveBeenCalledWith(null);
    expect(harness.messages["active-response"].done).toBe(false);
    expect(harness.markHistoryChanged).not.toHaveBeenCalled();
  });

  it("marks interrupted own responses done but leaves other users and pending ask-user turns alone", () => {
    const harness = createOptions();

    expect(reconcileLoadedChatTasks(harness.options as any)).toBe(true);
    expect(harness.setTaskIds).toHaveBeenCalledWith(null);
    expect(harness.messages["active-response"].done).toBe(true);
    expect(harness.messages["other-user-response"].done).toBe(false);
    expect(harness.messages["ask-user-response"].done).toBe(false);
    expect(harness.markHistoryChanged).toHaveBeenCalledOnce();
  });

  it("does nothing when the request is stale or task IDs changed during the load", () => {
    const staleRequest = createOptions({
      pendingTaskIds: ["task-new"],
      canReconcile: vi.fn(() => false),
    });
    expect(reconcileLoadedChatTasks(staleRequest.options as any)).toBe(false);
    expect(staleRequest.setTaskIds).not.toHaveBeenCalled();

    const updatedTasks = createOptions({ pendingTaskIds: ["task-new"] });
    updatedTasks.currentTaskIds.mockReturnValue(["new-live-task"]);
    expect(reconcileLoadedChatTasks(updatedTasks.options as any)).toBe(false);
    expect(updatedTasks.setTaskIds).not.toHaveBeenCalled();
  });
});
