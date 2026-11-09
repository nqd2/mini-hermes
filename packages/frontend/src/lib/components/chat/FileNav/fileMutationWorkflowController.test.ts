import { describe, expect, it, vi } from "vitest";
import type { FileMutationWorkflowDependencies } from "./fileMutationWorkflowController";
import { createFileMutationWorkflowController } from "./fileMutationWorkflowController";

type Dependencies = FileMutationWorkflowDependencies;

const createHarness = (overrides: Partial<Dependencies> = {}) => {
  const move = vi.fn(async (_source: string, _destinationFolder: string) => ({
    status: "invalid" as const,
  }));
  const rename = vi.fn(async (_oldPath: string, _newName: string) => ({
    status: "invalid" as const,
  }));
  const invalidateTreeCache = vi.fn();
  const clearSelection = vi.fn();
  const refreshBrowser = vi.fn(async () => undefined);
  const onMoveFailed = vi.fn();
  const onMoved = vi.fn();
  const onRenameFailed = vi.fn();
  const onRenamed = vi.fn();
  const dependencies: Dependencies = {
    move,
    rename,
    getCurrentPath: () => "/workspace/current/",
    sourceParentPath: (path) => path.slice(0, path.lastIndexOf("/") + 1),
    invalidateTreeCache,
    clearSelection,
    refreshBrowser,
    onMoveFailed,
    onMoved,
    onRenameFailed,
    onRenamed,
    ...overrides,
  };
  return {
    controller: createFileMutationWorkflowController(dependencies),
    move: dependencies.move,
    rename: dependencies.rename,
    invalidateTreeCache,
    clearSelection,
    refreshBrowser,
    onMoveFailed,
    onMoved,
    onRenameFailed,
    onRenamed,
  };
};

describe("FileNav mutation workflow controller", () => {
  it("continues sequential moves and refreshes once for successful paths only", async () => {
    const move = vi.fn(async (source: string, _destinationFolder: string) => {
      if (source.endsWith("first.txt")) {
        return {
          status: "moved",
          source,
          destination: "/workspace/dest/first.txt",
          name: "first.txt",
        } as const;
      }
      if (source.endsWith("second.txt"))
        return { status: "failed", error: "permission denied" } as const;
      return { status: "blocked" } as const;
    });
    const harness = createHarness({ move });

    await harness.controller.movePaths(
      ["/workspace/first.txt", "/workspace/second.txt", "/workspace/third.txt"],
      "/workspace/dest/",
    );

    expect(move.mock.calls.map(([source]) => source)).toEqual([
      "/workspace/first.txt",
      "/workspace/second.txt",
      "/workspace/third.txt",
    ]);
    expect(harness.onMoved).toHaveBeenCalledWith("first.txt");
    expect(harness.onMoveFailed).toHaveBeenCalledWith("permission denied");
    expect(harness.invalidateTreeCache).toHaveBeenCalledWith(
      "/workspace/current/",
      "/workspace/dest/",
      "/workspace/first.txt",
      "/workspace/",
    );
    expect(harness.clearSelection).toHaveBeenCalledOnce();
    expect(harness.refreshBrowser).toHaveBeenCalledOnce();
  });

  it("does not refresh when all move attempts are blocked or invalid", async () => {
    const harness = createHarness();

    await harness.controller.movePaths(
      ["/workspace/first.txt"],
      "/workspace/dest/",
    );

    expect(harness.invalidateTreeCache).not.toHaveBeenCalled();
    expect(harness.clearSelection).not.toHaveBeenCalled();
    expect(harness.refreshBrowser).not.toHaveBeenCalled();
  });

  it("refreshes after a failed rename because the remote result may be uncertain", async () => {
    const rename = vi.fn(
      async (_oldPath: string, _newName: string) =>
        ({
          status: "failed",
          error: "connection reset",
        }) as const,
    );
    const harness = createHarness({ rename });

    await harness.controller.renamePath("/workspace/old.txt", "new.txt");

    expect(harness.onRenameFailed).toHaveBeenCalledWith("connection reset");
    expect(harness.invalidateTreeCache).toHaveBeenCalledWith(
      "/workspace/current/",
      "/workspace/old.txt",
    );
    expect(harness.refreshBrowser).toHaveBeenCalledOnce();
  });

  it("skips cache changes for blocked or invalid rename results", async () => {
    const rename = vi.fn(
      async (_oldPath: string, _newName: string) =>
        ({
          status: "blocked",
        }) as const,
    );
    const harness = createHarness({ rename });

    await harness.controller.renamePath("/workspace/old.txt", "");

    expect(harness.invalidateTreeCache).not.toHaveBeenCalled();
    expect(harness.refreshBrowser).not.toHaveBeenCalled();
  });
});
