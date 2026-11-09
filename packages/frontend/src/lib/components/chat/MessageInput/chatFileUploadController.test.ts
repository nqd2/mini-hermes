import { describe, expect, it, vi } from "vitest";
import { createChatFileUploadController } from "./chatFileUploadController";

const createHarness = (overrides: Record<string, unknown> = {}) => {
  const attachments: any[] = [];
  const events: string[] = [];
  const terminal = { contexts: { chat: { context_id: "chat_id" } } };
  const dependencies = {
    canUpload: vi.fn(() => true),
    canUploadWithSelectedModels: vi.fn(() => true),
    getFilesystemTerminal: vi.fn(() => null as any),
    getSelectedTerminalId: vi.fn(() => "terminal-1"),
    getChatId: vi.fn(() => "chat-1" as string | null),
    isTemporaryChat: vi.fn(() => false),
    getDefaultUploadContext: vi.fn(() => false),
    getToken: vi.fn(() => "token"),
    getAudioLanguage: vi.fn(() => null as string | null),
    createId: vi.fn(() => "file-id"),
    translate: vi.fn((key: string) => key),
    showError: vi.fn((message: string) => events.push(`error:${message}`)),
    showWarning: vi.fn((message: string) => events.push(`warning:${message}`)),
    addAttachment: vi.fn((file: any) => attachments.push(file)),
    touchAttachments: vi.fn(() => events.push("touch")),
    removeAttachment: vi.fn((matches: (file: any) => boolean) => {
      const index = attachments.findIndex(matches);
      if (index >= 0) attachments.splice(index, 1);
    }),
    uploadFilesystem: vi.fn(async () => true),
    uploadToServer: vi.fn(async () => ({
      id: "uploaded-id",
      meta: { collection_name: "uploads", content_type: "application/pdf" },
    })),
    extractContent: vi.fn(async () => "temporary contents"),
    onUpdate: vi.fn((payload?: { file: any }) =>
      events.push(`update:${payload?.file.status}`),
    ),
    ...overrides,
  };

  return {
    controller: createChatFileUploadController(dependencies as any),
    dependencies,
    attachments,
    events,
    terminal,
    file: { name: "report.pdf", size: 50, type: "application/pdf" } as File,
  };
};

describe("createChatFileUploadController", () => {
  it("blocks users without upload permission or model support", async () => {
    const denied = createHarness({ canUpload: vi.fn(() => false) });
    await expect(denied.controller.upload(denied.file)).resolves.toBeNull();
    expect(denied.dependencies.showError).toHaveBeenCalledWith(
      "You do not have permission to upload files.",
    );
    expect(denied.dependencies.addAttachment).not.toHaveBeenCalled();

    const unsupported = createHarness({
      canUploadWithSelectedModels: vi.fn(() => false),
    });
    await expect(
      unsupported.controller.upload(unsupported.file),
    ).resolves.toBeNull();
    expect(unsupported.dependencies.showError).toHaveBeenCalledWith(
      "Model(s) do not support file upload",
    );
    expect(unsupported.dependencies.uploadToServer).not.toHaveBeenCalled();
  });

  it("rejects an empty file before adding it to the attachment collection", async () => {
    const harness = createHarness();

    await expect(
      harness.controller.upload({ ...harness.file, size: 0 } as File),
    ).resolves.toBeNull();

    expect(harness.dependencies.showError).toHaveBeenCalledWith(
      "You cannot upload an empty file.",
    );
    expect(harness.dependencies.addAttachment).not.toHaveBeenCalled();
  });

  it("keeps a filesystem attachment pending until a chat-scoped upload can run", async () => {
    const harness = createHarness({
      getFilesystemTerminal: vi.fn(() => harness.terminal),
      getChatId: vi.fn(() => null),
    });

    await expect(
      harness.controller.upload(harness.file),
    ).resolves.toBeUndefined();

    expect(harness.attachments[0]).toMatchObject({
      type: "filesystem",
      status: "pending",
      session_id: null,
      terminal_id: "terminal-1",
    });
    expect(harness.dependencies.touchAttachments).toHaveBeenCalledOnce();
    expect(harness.dependencies.uploadFilesystem).not.toHaveBeenCalled();
    expect(harness.dependencies.uploadToServer).not.toHaveBeenCalled();
  });

  it("uploads filesystem attachments through the terminal adapter and removes failures", async () => {
    const harness = createHarness({
      getFilesystemTerminal: vi.fn(() => harness.terminal),
      getChatId: vi.fn(() => "chat-7"),
    });

    await harness.controller.upload(harness.file);

    expect(harness.dependencies.uploadFilesystem).toHaveBeenCalledWith(
      expect.objectContaining({ type: "filesystem", session_id: "chat-7" }),
      "chat-7",
    );
    expect(harness.dependencies.onUpdate).toHaveBeenCalledOnce();
    expect(harness.dependencies.uploadToServer).not.toHaveBeenCalled();

    const failed = createHarness({
      getFilesystemTerminal: vi.fn(() => harness.terminal),
      uploadFilesystem: vi.fn(async () => false),
    });
    await failed.controller.upload(failed.file);
    expect(failed.attachments).toHaveLength(0);
    expect(failed.dependencies.onUpdate).toHaveBeenCalledOnce();
  });

  it("adds upload metadata and copies the stored file reference", async () => {
    const harness = createHarness({
      getDefaultUploadContext: vi.fn(() => true),
      getAudioLanguage: vi.fn(() => "vi"),
    });
    const file = {
      ...harness.file,
      name: "lecture.mp3",
      type: "audio/mpeg",
    } as File;

    await harness.controller.upload(file);
    const result = harness.attachments[0];

    expect(harness.dependencies.uploadToServer).toHaveBeenCalledWith(
      "token",
      file,
      { language: "vi" },
      true,
    );
    expect(result).toMatchObject({
      context: "full",
      type: "file",
      status: "uploaded",
      id: "uploaded-id",
      collection_name: "uploads",
      content_type: "application/pdf",
      url: "uploaded-id",
    });
    expect(harness.dependencies.onUpdate).toHaveBeenCalledWith({
      file: result,
    });
  });

  it("marks Office files for chat extraction during server upload", async () => {
    const harness = createHarness();

    await harness.controller.upload({
      ...harness.file,
      name: "programs.xlsx",
    } as File);

    expect(harness.dependencies.uploadToServer).toHaveBeenCalledWith(
      "token",
      expect.objectContaining({ name: "programs.xlsx" }),
      { mini_hermes_chat_upload: true },
      true,
    );
  });

  it("marks a failed server upload as an error and removes the attachment", async () => {
    const harness = createHarness({ uploadToServer: vi.fn(async () => null) });

    await harness.controller.upload(harness.file);
    const result = harness.dependencies.addAttachment.mock.calls[0][0];

    expect(result).toMatchObject({
      status: "error",
      error: "Failed to upload file.",
    });
    expect(harness.attachments).toHaveLength(0);
    expect(harness.dependencies.onUpdate).toHaveBeenCalledWith({
      file: result,
    });
  });

  it("extracts temporary chat files into text attachments without server upload", async () => {
    const harness = createHarness({ isTemporaryChat: vi.fn(() => true) });

    await harness.controller.upload(harness.file);
    const result = harness.attachments[0];

    expect(result).toMatchObject({
      type: "text",
      status: "uploaded",
      content: "temporary contents",
      id: "file-id",
    });
    expect(harness.dependencies.uploadToServer).not.toHaveBeenCalled();
    expect(harness.dependencies.onUpdate).toHaveBeenCalledWith({
      file: result,
    });
  });

  it("reports temporary extraction failure and removes the broken attachment", async () => {
    const harness = createHarness({
      isTemporaryChat: vi.fn(() => true),
      extractContent: vi.fn(async () => {
        throw new Error("extract failed");
      }),
    });

    await expect(harness.controller.upload(harness.file)).resolves.toBeNull();

    expect(harness.dependencies.showError).toHaveBeenCalledWith(
      "Failed to extract content from the file: {{error}}",
    );
    expect(harness.attachments).toHaveLength(0);
    expect(harness.dependencies.onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        file: expect.objectContaining({ status: "error" }),
      }),
    );
  });
});
