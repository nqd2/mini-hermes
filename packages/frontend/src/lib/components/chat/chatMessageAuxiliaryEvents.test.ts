import { describe, expect, it, vi } from "vitest";
import { handleChatMessageAuxiliaryEvent } from "./chatMessageAuxiliaryEvents";

const notifications = () => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
});

describe("handleChatMessageAuxiliaryEvent", () => {
  it("appends citations to the active message", () => {
    const message = { sources: [{ id: "source-1" }] };

    expect(
      handleChatMessageAuxiliaryEvent(
        "citation",
        { id: "source-2" },
        message,
        {},
        notifications(),
      ),
    ).toBe("message-updated");
    expect(message.sources).toEqual([{ id: "source-1" }, { id: "source-2" }]);
  });

  it("upserts code execution evidence by execution ID", () => {
    const message = { code_executions: [{ id: "exec-1", output: "old" }] };

    handleChatMessageAuxiliaryEvent(
      "source",
      { type: "code_execution", id: "exec-1", output: "new" },
      message,
      {},
      notifications(),
    );
    expect(message.code_executions).toEqual([
      { type: "code_execution", id: "exec-1", output: "new" },
    ]);

    handleChatMessageAuxiliaryEvent(
      "source",
      { type: "code_execution", id: "exec-2" },
      message,
      {},
      notifications(),
    );
    expect(message.code_executions).toHaveLength(2);
  });

  it("updates favorite state on the message", () => {
    const message = { id: "message-1", favorite: false };

    expect(
      handleChatMessageAuxiliaryEvent(
        "chat:message:favorite",
        { favorite: true },
        message,
        {},
        notifications(),
      ),
    ).toBe("message-updated");
    expect(message.favorite).toBe(true);
  });

  it("routes server notifications to the matching toast adapter", () => {
    const notify = notifications();

    expect(
      handleChatMessageAuxiliaryEvent(
        "notification",
        { type: "warning", content: "Partial result" },
        {},
        {},
        notify,
      ),
    ).toBe("message-updated");
    expect(notify.warning).toHaveBeenCalledWith("Partial result");
    expect(notify.info).not.toHaveBeenCalled();
  });

  it("patches outlet messages and preserves the original text for changed content", () => {
    const original = { id: "message-1", content: "before" };
    const unchanged = { id: "message-2", content: "same" };
    const messages = { "message-1": original, "message-2": unchanged };

    expect(
      handleChatMessageAuxiliaryEvent(
        "chat:outlet",
        {
          messages: [
            { id: "message-1", content: "after" },
            { id: "message-2", content: "same" },
            { id: "message-missing", content: "ignored" },
          ],
        },
        null,
        messages,
        notifications(),
      ),
    ).toBe("history-patched");
    expect(messages["message-1"]).toEqual({
      id: "message-1",
      content: "after",
      originalContent: "before",
    });
    expect(messages["message-2"]).toBe(unchanged);
  });

  it("leaves unrelated event types to the main chat handler", () => {
    expect(
      handleChatMessageAuxiliaryEvent(
        "chat:completion",
        {},
        {},
        {},
        notifications(),
      ),
    ).toBeNull();
  });
});
