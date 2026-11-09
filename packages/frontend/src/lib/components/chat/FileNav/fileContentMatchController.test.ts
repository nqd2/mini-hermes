import { describe, expect, it, vi } from "vitest";
import type {
  TerminalContentMatch,
  TerminalFileMatch,
} from "$lib/apis/terminal";
import type { FileContentMatchControllerDependencies } from "./fileContentMatchController";
import { createFileContentMatchController } from "./fileContentMatchController";

const fileMatch: TerminalFileMatch = {
  path: "/workspace/report.txt",
  relative_path: "report.txt",
  name: "report.txt",
  type: "file",
  name_match: false,
  content_matches: [],
};

const contentMatch: TerminalContentMatch = {
  line: 12,
  column: 4,
  text: "matched text",
};

const createController = (
  overrides: Partial<FileContentMatchControllerDependencies> = {},
) => {
  const openFileMatch = vi.fn(async () => true);
  const setSearchTarget = vi.fn();
  let requestId = 0;
  const dependencies: FileContentMatchControllerDependencies = {
    openFileMatch,
    getSearchLength: () => 5,
    nextRequestId: () => ++requestId,
    setSearchTarget,
    ...overrides,
  };
  return {
    controller: createFileContentMatchController(dependencies),
    openFileMatch: dependencies.openFileMatch,
    setSearchTarget,
  };
};

describe("FileNav content-match controller", () => {
  it("sets the highlight target only after opening the matching file", async () => {
    const harness = createController();

    await expect(
      harness.controller.openContentMatch(fileMatch, contentMatch),
    ).resolves.toBe(true);

    expect(harness.openFileMatch).toHaveBeenCalledWith(fileMatch);
    expect(harness.setSearchTarget).toHaveBeenCalledWith({
      line: 12,
      column: 4,
      length: 5,
      requestId: 1,
    });
  });

  it("does not set a highlight target when the file did not open", async () => {
    const openFileMatch = vi.fn(async () => false);
    const harness = createController({ openFileMatch });

    await expect(
      harness.controller.openContentMatch(fileMatch, contentMatch),
    ).resolves.toBe(false);

    expect(harness.setSearchTarget).not.toHaveBeenCalled();
  });

  it("does not mark a target when opening rejects", async () => {
    const openFileMatch = vi.fn(async () =>
      Promise.reject(new Error("preview failed")),
    );
    const harness = createController({ openFileMatch });

    await expect(
      harness.controller.openContentMatch(fileMatch, contentMatch),
    ).rejects.toThrow("preview failed");

    expect(harness.setSearchTarget).not.toHaveBeenCalled();
  });
});
