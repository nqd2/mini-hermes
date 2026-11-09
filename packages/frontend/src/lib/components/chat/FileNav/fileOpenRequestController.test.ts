import { describe, expect, it, vi } from "vitest";
import {
  prepareFileOpenRequest,
  type FileOpenRequestEntry,
} from "./fileOpenRequestController";

const fileEntry: FileOpenRequestEntry = {
  name: "report.xlsx",
  type: "file",
  fullPath: "/workspace/report.xlsx",
  parentPath: "/workspace",
};

const makeDependencies = (overrides = {}) => ({
  isDisposed: vi.fn(() => false),
  getCurrentPath: vi.fn(() => "/workspace"),
  getInitialFilePath: vi.fn(() => undefined),
  hasExternalOpenHandler: vi.fn(() => false),
  openExternalFile: vi.fn(),
  entryPath: vi.fn(
    (path: string, entry: { name: string }) => `${path}/${entry.name}`,
  ),
  normalizePage: vi.fn((page: unknown) => (page == null ? null : Number(page))),
  isDirty: vi.fn(() => false),
  isSaving: vi.fn(() => false),
  markDiskChanged: vi.fn(),
  protectBuffer: vi.fn(async () => true),
  loadDir: vi.fn(async () => {}),
  ...overrides,
});

describe("prepareFileOpenRequest", () => {
  it("loads a directory and stops before file-buffer or preview work", async () => {
    const dependencies = makeDependencies();

    const result = await prepareFileOpenRequest(
      { name: "reports", type: "directory", fullPath: "/workspace/reports" },
      {},
      dependencies,
    );

    expect(result).toEqual({ status: "handled" });
    expect(dependencies.loadDir).toHaveBeenCalledWith("/workspace/reports");
    expect(dependencies.protectBuffer).not.toHaveBeenCalled();
  });

  it("routes an external file open with a normalized page and skips the local buffer", async () => {
    const dependencies = makeDependencies({
      hasExternalOpenHandler: vi.fn(() => true),
    });

    const result = await prepareFileOpenRequest(
      fileEntry,
      { page: "7" },
      dependencies,
    );

    expect(result).toEqual({ status: "handled" });
    expect(dependencies.normalizePage).toHaveBeenCalledWith("7");
    expect(dependencies.openExternalFile).toHaveBeenCalledWith(
      "/workspace/report.xlsx",
      7,
    );
    expect(dependencies.protectBuffer).not.toHaveBeenCalled();
  });

  it("protects an edited buffer before a refresh and marks disk changes when blocked", async () => {
    const dependencies = makeDependencies({
      isDirty: vi.fn(() => true),
    });

    const result = await prepareFileOpenRequest(
      fileEntry,
      { refresh: true },
      dependencies,
    );

    expect(result).toEqual({ status: "blocked" });
    expect(dependencies.markDiskChanged).toHaveBeenCalledOnce();
    expect(dependencies.protectBuffer).not.toHaveBeenCalled();
  });

  it("stops when the user cancels buffer protection", async () => {
    const dependencies = makeDependencies({
      protectBuffer: vi.fn(async () => false),
    });

    const result = await prepareFileOpenRequest(fileEntry, {}, dependencies);

    expect(result).toEqual({ status: "blocked" });
    expect(dependencies.loadDir).not.toHaveBeenCalled();
  });

  it("loads the parent directory before returning a ready file path", async () => {
    const dependencies = makeDependencies();

    const result = await prepareFileOpenRequest(
      {
        ...fileEntry,
        fullPath: "/workspace/child/report.xlsx",
        parentPath: "/workspace/child",
      },
      {},
      dependencies,
    );

    expect(dependencies.protectBuffer).toHaveBeenCalledOnce();
    expect(dependencies.loadDir).toHaveBeenCalledWith("/workspace/child");
    expect(result).toEqual({
      status: "ready",
      fullPath: "/workspace/child/report.xlsx",
      parentPath: "/workspace/child",
    });
  });

  it("rechecks the live directory after buffer protection completes", async () => {
    let currentPath = "/workspace";
    const dependencies = makeDependencies({
      getCurrentPath: vi.fn(() => currentPath),
      protectBuffer: vi.fn(async () => {
        currentPath = "/another-directory";
        return true;
      }),
    });

    const result = await prepareFileOpenRequest(fileEntry, {}, dependencies);

    expect(dependencies.loadDir).toHaveBeenCalledWith("/workspace");
    expect(result).toEqual({
      status: "ready",
      fullPath: "/workspace/report.xlsx",
      parentPath: "/workspace",
    });
  });

  it("ignores requests after FileNav has been disposed", async () => {
    const dependencies = makeDependencies({
      isDisposed: vi.fn(() => true),
    });

    const result = await prepareFileOpenRequest(fileEntry, {}, dependencies);

    expect(result).toEqual({ status: "ignored" });
    expect(dependencies.protectBuffer).not.toHaveBeenCalled();
    expect(dependencies.loadDir).not.toHaveBeenCalled();
  });
});
