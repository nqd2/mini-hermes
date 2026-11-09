import type {
  TerminalContentMatch,
  TerminalFileMatch,
} from "$lib/apis/terminal";

export type FileContentSearchTarget = {
  line: number;
  column: number;
  length: number;
  requestId: number;
};

export type FileContentMatchControllerDependencies = {
  openFileMatch: (match: TerminalFileMatch) => Promise<boolean>;
  getSearchLength: () => number;
  nextRequestId: () => number;
  setSearchTarget: (target: FileContentSearchTarget) => void;
};

/** Opens a search result and only marks its text after the matching file is ready. */
export const createFileContentMatchController = (
  dependencies: FileContentMatchControllerDependencies,
) => {
  const openContentMatch = async (
    match: TerminalFileMatch,
    contentMatch: TerminalContentMatch,
  ): Promise<boolean> => {
    if (!(await dependencies.openFileMatch(match))) return false;

    dependencies.setSearchTarget({
      line: contentMatch.line,
      column: contentMatch.column,
      length: dependencies.getSearchLength(),
      requestId: dependencies.nextRequestId(),
    });
    return true;
  };

  return { openContentMatch };
};
