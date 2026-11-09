import { describe, expect, it, vi } from "vitest";
import type { FileDownloadControllerDependencies } from "./fileDownloadController";
import { createFileDownloadController } from "./fileDownloadController";

const terminal = { url: "https://terminal.example", key: "secret" };
const result = { blob: new Blob(["file"]), filename: "report.txt" };

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
};

const createHarness = (
  overrides: Partial<FileDownloadControllerDependencies<string>> = {},
) => {
  let currentTerminal: typeof terminal | null = terminal;
  const downloadFile = vi.fn(async () => result);
  const archiveDirectory = vi.fn(async () => result);
  const setDownloading = vi.fn();
  const showPreparingToast = vi.fn(() => "toast-id");
  const showDownloadFailed = vi.fn();
  const dismissToast = vi.fn();
  const createObjectUrl = vi.fn(() => "blob:download");
  const clickDownload = vi.fn();
  const revokeObjectUrl = vi.fn();
  const dependencies: FileDownloadControllerDependencies<string> = {
    getTerminal: () => currentTerminal,
    getSessionId: () => "chat-1",
    downloadFile,
    archiveDirectory,
    setDownloading,
    showPreparingToast,
    showDownloadFailed,
    dismissToast,
    createObjectUrl,
    clickDownload,
    revokeObjectUrl,
    ...overrides,
  };

  return {
    controller: createFileDownloadController(dependencies),
    dependencies,
    archiveDirectory,
    downloadFile,
    setDownloading,
    showPreparingToast,
    showDownloadFailed,
    dismissToast,
    createObjectUrl,
    clickDownload,
    revokeObjectUrl,
    setTerminal: (value: typeof terminal | null) => (currentTerminal = value),
  };
};

describe("FileNav download controller", () => {
  it("ignores requests without a selected terminal", async () => {
    const harness = createHarness();
    harness.setTerminal(null);

    await harness.controller.download("/report.txt");

    expect(harness.downloadFile).not.toHaveBeenCalled();
    expect(harness.showPreparingToast).not.toHaveBeenCalled();
    expect(harness.setDownloading).not.toHaveBeenCalled();
  });

  it("downloads one file and releases the object URL", async () => {
    const harness = createHarness();

    await harness.controller.download("/folder/report.txt");

    expect(harness.downloadFile).toHaveBeenCalledWith(
      terminal.url,
      terminal.key,
      "/folder/report.txt",
      "chat-1",
    );
    expect(harness.archiveDirectory).not.toHaveBeenCalled();
    expect(harness.createObjectUrl).toHaveBeenCalledWith(result.blob);
    expect(harness.clickDownload).toHaveBeenCalledWith(
      "blob:download",
      "report.txt",
    );
    expect(harness.revokeObjectUrl).toHaveBeenCalledWith("blob:download");
    expect(harness.setDownloading.mock.calls).toEqual([[true], [false]]);
    expect(harness.dismissToast).toHaveBeenCalledWith("toast-id");
  });

  it("archives a directory after removing its trailing slash", async () => {
    const harness = createHarness();

    await harness.controller.download("/folder/");

    expect(harness.archiveDirectory).toHaveBeenCalledWith(
      terminal.url,
      terminal.key,
      ["/folder"],
      "chat-1",
    );
    expect(harness.downloadFile).not.toHaveBeenCalled();
  });

  it("revokes the temporary URL when the browser download trigger fails", async () => {
    const error = new Error("download click failed");
    const clickDownload = vi.fn(() => {
      throw error;
    });
    const harness = createHarness({ clickDownload });

    await expect(harness.controller.download("/report.txt")).rejects.toBe(
      error,
    );

    expect(harness.revokeObjectUrl).toHaveBeenCalledWith("blob:download");
    expect(harness.dismissToast).toHaveBeenCalledWith("toast-id");
    expect(harness.setDownloading.mock.calls).toEqual([[true], [false]]);
  });

  it("reports an empty response and always dismisses the progress toast", async () => {
    const harness = createHarness({ downloadFile: vi.fn(async () => null) });

    await harness.controller.download("/missing.txt");

    expect(harness.showDownloadFailed).toHaveBeenCalledOnce();
    expect(harness.createObjectUrl).not.toHaveBeenCalled();
    expect(harness.dismissToast).toHaveBeenCalledWith("toast-id");
    expect(harness.setDownloading.mock.calls).toEqual([[true], [false]]);
  });

  it("serializes concurrent downloads and releases the lock after completion", async () => {
    const pending = deferred<typeof result | null>();
    const downloadFile = vi.fn(() => pending.promise);
    const harness = createHarness({ downloadFile });
    const first = harness.controller.download("/first.txt");
    await harness.controller.download("/second.txt");

    expect(downloadFile).toHaveBeenCalledTimes(1);
    pending.resolve(result);
    await first;
    await harness.controller.download("/second.txt");
    expect(downloadFile).toHaveBeenCalledTimes(2);
  });

  it("clears the busy state and dismisses the toast when a request rejects", async () => {
    const error = new Error("terminal unavailable");
    const harness = createHarness({
      downloadFile: vi.fn(async () => Promise.reject(error)),
    });

    await expect(harness.controller.download("/report.txt")).rejects.toBe(
      error,
    );

    expect(harness.dismissToast).toHaveBeenCalledWith("toast-id");
    expect(harness.setDownloading.mock.calls).toEqual([[true], [false]]);
  });
});
