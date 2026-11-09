import type { FileEntry } from "$lib/apis/terminal";

export type FileOpenRequestEntry = FileEntry & {
  fullPath?: string;
  parentPath?: string;
};

export type FileOpenRequestOptions = {
  page?: unknown;
  refresh?: boolean;
};

export type FileOpenRequestDependencies = {
  isDisposed: () => boolean;
  getCurrentPath: () => string;
  getInitialFilePath: () => string | undefined;
  hasExternalOpenHandler: () => boolean;
  openExternalFile: (path: string, page: number | null) => void;
  entryPath: (currentPath: string, entry: FileOpenRequestEntry) => string;
  normalizePage: (page: unknown) => number | null;
  isDirty: () => boolean;
  isSaving: () => boolean;
  markDiskChanged: () => void;
  protectBuffer: () => Promise<boolean>;
  loadDir: (path: string) => Promise<unknown>;
};

export type FileOpenRequestResult =
  | { status: "ignored" | "handled" | "blocked" }
  | { status: "ready"; fullPath: string; parentPath: string };

/** Resolves navigation and buffer-safety gates before FileNav loads a preview. */
export const prepareFileOpenRequest = async (
  entry: FileOpenRequestEntry,
  options: FileOpenRequestOptions,
  dependencies: FileOpenRequestDependencies,
): Promise<FileOpenRequestResult> => {
  if (dependencies.isDisposed()) return { status: "ignored" };

  const currentPath = dependencies.getCurrentPath();
  const fullPath = entry.fullPath ?? dependencies.entryPath(currentPath, entry);
  const parentPath = entry.parentPath ?? currentPath;

  if (entry.type === "directory") {
    await dependencies.loadDir(fullPath);
    return { status: "handled" };
  }

  if (
    dependencies.hasExternalOpenHandler() &&
    !options.refresh &&
    fullPath !== dependencies.getInitialFilePath()
  ) {
    dependencies.openExternalFile(
      fullPath,
      dependencies.normalizePage(options.page),
    );
    return { status: "handled" };
  }

  if (options.refresh && (dependencies.isDirty() || dependencies.isSaving())) {
    dependencies.markDiskChanged();
    return { status: "blocked" };
  }

  if (!(await dependencies.protectBuffer())) return { status: "blocked" };
  if (parentPath !== dependencies.getCurrentPath()) {
    await dependencies.loadDir(parentPath);
  }

  return { status: "ready", fullPath, parentPath };
};
