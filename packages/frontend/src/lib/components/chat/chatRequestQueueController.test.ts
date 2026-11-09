import { describe, expect, it, vi } from "vitest";
import {
  createChatRequestQueueController,
  type ChatRequestQueueControllerDependencies,
} from "./chatRequestQueueController";

type FileRef = { id: string; status?: string; type?: string };
type QueueEntry = {
  id: string;
  prompt: string;
  files: FileRef[];
  error?: string;
};
type Options = ChatRequestQueueControllerDependencies<FileRef>;

const createHarness = (initialQueue: QueueEntry[] = []) => {
  let chatId = "chat-1";
  let embedded = false;
  let lastMessage: { role?: string; done?: boolean } | null = null;
  let queues: Record<string, QueueEntry[]> = { "chat-1": initialQueue };
  const events: string[] = [];
  const updateQueues = vi.fn(
    (update: Parameters<Options["updateQueues"]>[0]) => {
      queues = update(queues);
    },
  );
  const submit = vi.fn(async (prompt: string, files: FileRef[]) => {
    events.push(`submit:${prompt}:${files.map((file) => file.id).join(",")}`);
  });
  const stopResponse = vi.fn(async (processQueue: boolean) => {
    events.push(`stop:${processQueue}`);
  });
  const waitForRender = vi.fn(async () => {
    events.push("render");
  });
  const setInputFiles = vi.fn();
  const setInputText = vi.fn();
  const showSendError = vi.fn();
  const logProcessingError = vi.fn();
  const options: Options = {
    getQueue: (targetChatId) => queues[targetChatId] ?? [],
    getCurrentChatId: () => chatId,
    getLastMessage: () => lastMessage,
    isEmbedded: () => embedded,
    updateQueues,
    submit,
    stopResponse,
    waitForRender,
    setInputFiles,
    setInputText,
    showSendError,
    logProcessingError,
  };

  return {
    controller: createChatRequestQueueController(options),
    events,
    submit,
    stopResponse,
    waitForRender,
    setInputFiles,
    setInputText,
    showSendError,
    logProcessingError,
    getQueue: (targetChatId = chatId) => queues[targetChatId] ?? [],
    setQueue: (targetChatId: string, queue: QueueEntry[]) => {
      queues = { ...queues, [targetChatId]: queue };
    },
    setChatId: (id: string) => {
      chatId = id;
    },
    setEmbedded: (value: boolean) => {
      embedded = value;
    },
    setLastMessage: (message: { role?: string; done?: boolean } | null) => {
      lastMessage = message;
    },
  };
};

const entry = (id: string, prompt = id, files: FileRef[] = []): QueueEntry => ({
  id,
  prompt,
  files,
});

describe("chat request queue controller", () => {
  it("combines queued prompts up to the filesystem boundary", async () => {
    const first = entry("first", "first prompt", [{ id: "a" }]);
    const second = entry("second", "second prompt", [{ id: "b" }]);
    const filesystem = entry("filesystem", "filesystem prompt", [
      { id: "terminal-file", type: "filesystem" },
    ]);
    const after = entry("after");
    const harness = createHarness([first, second, filesystem, after]);

    await harness.controller.processNext("chat-1");

    expect(harness.submit).toHaveBeenCalledWith(
      "first prompt\n\nsecond prompt",
      [{ id: "a" }, { id: "b" }],
    );
    expect(harness.getQueue()).toEqual([filesystem, after]);
  });

  it("sends one queued entry at a time in an embedded chat", async () => {
    const first = entry("first");
    const second = entry("second");
    const harness = createHarness([first, second]);
    harness.setEmbedded(true);

    await harness.controller.processNext("chat-1");

    expect(harness.submit).toHaveBeenCalledWith("first", []);
    expect(harness.getQueue()).toEqual([second]);
  });

  it("does not process a stale chat or a queue with incomplete file uploads", async () => {
    const harness = createHarness([
      entry("upload", "upload", [{ id: "f", status: "uploading" }]),
    ]);

    harness.setChatId("chat-2");
    await harness.controller.processNext("chat-1");
    harness.setChatId("chat-1");
    await harness.controller.processNext("chat-1");

    expect(harness.submit).not.toHaveBeenCalled();
    expect(harness.getQueue()).toHaveLength(1);
  });

  it("restores a failed batch ahead of requests queued while it was running", async () => {
    const first = entry("first");
    const second = entry("second");
    const harness = createHarness([first, second]);
    const error = new Error("provider failed");
    harness.submit.mockImplementation(async () => {
      harness.setQueue("chat-1", [entry("new")]);
      throw error;
    });

    await harness.controller.processNext("chat-1");

    expect(harness.logProcessingError).toHaveBeenCalledWith(error);
    expect(harness.getQueue()).toEqual([
      { ...first, error: "Error: provider failed" },
      { ...second, error: "Error: provider failed" },
      entry("new"),
    ]);
  });

  it("serializes queue processing for the same chat", async () => {
    const harness = createHarness([entry("first"), entry("second")]);
    let releaseSubmission!: () => void;
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    harness.submit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          releaseSubmission = resolve;
          markStarted();
        }),
    );

    const firstAttempt = harness.controller.processNext("chat-1");
    await started;
    await harness.controller.processNext("chat-1");

    expect(harness.submit).toHaveBeenCalledTimes(1);
    releaseSubmission();
    await firstAttempt;
  });

  it("stops before sending a selected queue item and then processes the next item", async () => {
    const harness = createHarness([entry("first"), entry("selected")]);

    await harness.controller.sendNow("selected");

    expect(harness.events).toEqual([
      "stop:false",
      "render",
      "submit:selected:",
      "submit:first:",
    ]);
    expect(harness.getQueue()).toEqual([]);
  });

  it("restores a queue item to the composer when edited", async () => {
    const harness = createHarness([
      entry("edit", "revise this", [{ id: "file" }]),
      entry("next"),
    ]);

    await harness.controller.edit("edit");

    expect(harness.setInputFiles).toHaveBeenCalledWith([{ id: "file" }]);
    expect(harness.setInputText).toHaveBeenCalledWith("revise this");
    expect(harness.getQueue()).toEqual([]);
    expect(harness.submit).toHaveBeenCalledWith("next", []);
  });

  it("removes a queue item and continues with the next request", async () => {
    const harness = createHarness([entry("delete"), entry("next")]);

    await harness.controller.remove("delete");

    expect(harness.submit).toHaveBeenCalledWith("next", []);
    expect(harness.getQueue()).toEqual([]);
  });
});
