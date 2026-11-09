import type { FileMutationResult } from "./fileMutationController";

export type FileMutationWorkflowDependencies = {
  move: (
    source: string,
    destinationFolder: string,
  ) => Promise<FileMutationResult>;
  rename: (oldPath: string, newName: string) => Promise<FileMutationResult>;
  getCurrentPath: () => string;
  sourceParentPath: (source: string) => string;
  invalidateTreeCache: (...paths: string[]) => void;
  clearSelection: () => void;
  refreshBrowser: () => Promise<unknown>;
  onMoveFailed: (error: string) => void;
  onMoved: (name: string) => void;
  onRenameFailed: (error: string) => void;
  onRenamed: (name: string) => void;
};

/** Coordinates terminal mutations with FileNav cache, selection and user feedback. */
export const createFileMutationWorkflowController = (
  dependencies: FileMutationWorkflowDependencies,
) => {
  const movePaths = async (
    sources: readonly string[],
    destinationFolder: string,
  ): Promise<void> => {
    const movedSources: string[] = [];
    for (const source of sources) {
      const result = await dependencies.move(source, destinationFolder);
      if (result.status === "failed") {
        dependencies.onMoveFailed(result.error);
        continue;
      }
      if (result.status !== "moved") continue;

      dependencies.onMoved(result.name);
      movedSources.push(source);
    }

    if (movedSources.length === 0) return;

    dependencies.invalidateTreeCache(
      dependencies.getCurrentPath(),
      destinationFolder,
      ...movedSources,
      ...movedSources.map(dependencies.sourceParentPath),
    );
    dependencies.clearSelection();
    await dependencies.refreshBrowser();
  };

  const renamePath = async (
    oldPath: string,
    newName: string,
  ): Promise<void> => {
    const result = await dependencies.rename(oldPath, newName);
    if (result.status === "blocked" || result.status === "invalid") return;

    if (result.status === "failed") dependencies.onRenameFailed(result.error);
    else dependencies.onRenamed(result.name);

    dependencies.invalidateTreeCache(dependencies.getCurrentPath(), oldPath);
    await dependencies.refreshBrowser();
  };

  return { movePaths, renamePath };
};
