export type BulkDownloadPlan =
  { kind: "file"; path: string } | { kind: "archive"; paths: string[] };

export const createBulkDownloadPlan = (
  paths: readonly string[],
): BulkDownloadPlan | null => {
  if (paths.length === 0) return null;
  if (paths.length === 1 && !paths[0].endsWith("/")) {
    return { kind: "file", path: paths[0] };
  }
  return {
    kind: "archive",
    paths: paths.map((path) => path.replace(/\/$/, "")),
  };
};

export const deleteSelectedFilePaths = async (
  paths: readonly string[],
  deletePath: (path: string) => Promise<unknown>,
) => {
  let deleted = 0;
  for (const path of paths) {
    if (await deletePath(path.replace(/\/$/, ""))) deleted++;
  }
  return { deleted, total: paths.length };
};
