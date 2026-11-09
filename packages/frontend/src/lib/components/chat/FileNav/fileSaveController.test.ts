import { describe, expect, it, vi } from "vitest";
import { createFileSaveController } from "./fileSaveController";

describe("createFileSaveController", () => {
  type Dependencies = Parameters<typeof createFileSaveController>[0];
  const connection = {
    url: "http://terminal.test",
    key: "terminal-key",
    chatId: "chat-1",
    path: "/workspace/report.md",
  };

  const createController = (overrides: Partial<Dependencies> = {}) => {
    const getConnection = vi.fn(() => connection);
    const isWritable = vi.fn(() => true);
    const uploadFile = vi.fn(
      async (..._args: Parameters<Dependencies["uploadFile"]>) => ({
        path: "/workspace/report.md",
        size: 14,
      }),
    );
    const onSaved = vi.fn();
    const nextTick = vi.fn(async () => {});
    const controller = createFileSaveController({
      getConnection,
      isWritable,
      uploadFile,
      onSaved,
      nextTick,
      ...overrides,
    });

    return {
      controller,
      getConnection,
      isWritable,
      uploadFile,
      onSaved,
      nextTick,
    };
  };

  it("uploads the edited file to its directory and updates the open buffer", async () => {
    const { controller, uploadFile, onSaved, nextTick } = createController();

    await controller.save("updated report");

    expect(uploadFile).toHaveBeenCalledOnce();
    const [url, key, directory, file, chatId] = uploadFile.mock.calls[0]!;
    expect([url, key, directory, chatId]).toEqual([
      "http://terminal.test",
      "terminal-key",
      "/workspace/",
      "chat-1",
    ]);
    expect(file.name).toBe("report.md");
    expect(await file.text()).toBe("updated report");
    expect(onSaved).toHaveBeenCalledWith("updated report");
    expect(nextTick).toHaveBeenCalledOnce();
  });

  it("does not replace the open buffer when terminal or file changes during upload", async () => {
    const nextConnection = { ...connection, path: "/workspace/other.md" };
    const getConnection = vi
      .fn()
      .mockReturnValueOnce(connection)
      .mockReturnValueOnce(nextConnection);
    const { controller, onSaved } = createController({ getConnection });

    await controller.save("updated report");

    expect(onSaved).not.toHaveBeenCalled();
  });

  it("keeps the editor buffer unchanged when the terminal rejects the upload", async () => {
    const uploadFile = vi.fn(
      async (..._args: Parameters<Dependencies["uploadFile"]>) => null,
    );
    const { controller, onSaved, nextTick } = createController({ uploadFile });

    await expect(controller.save("updated report")).rejects.toThrow(
      "Failed to save file. Your changes are still in the editor.",
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(nextTick).not.toHaveBeenCalled();
    expect(uploadFile).toHaveBeenCalledOnce();
  });

  it("rejects unwritable files before uploading", async () => {
    const { controller, uploadFile } = createController({
      isWritable: vi.fn(() => false),
    });

    await expect(controller.save("updated report")).rejects.toThrow(
      "File is not writable.",
    );
    expect(uploadFile).not.toHaveBeenCalled();
  });
});
