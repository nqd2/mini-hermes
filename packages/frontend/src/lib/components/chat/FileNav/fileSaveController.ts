export type FileSaveConnection = {
  url: string;
  key: string;
  chatId: string | null;
  path: string;
};

export type FileSaveControllerDependencies = {
  getConnection: () => FileSaveConnection | null;
  isWritable: () => boolean;
  uploadFile: (
    url: string,
    key: string,
    directory: string,
    file: File,
    chatId?: string,
  ) => Promise<{ path: string; size: number } | null>;
  onSaved: (content: string) => void;
  nextTick: () => Promise<unknown>;
};

/** Saves the active text buffer while keeping stale requests from replacing a newer file. */
export const createFileSaveController = (
  dependencies: FileSaveControllerDependencies,
) => {
  const save = async (content: string): Promise<void> => {
    const target = dependencies.getConnection();
    if (!target || !dependencies.isWritable())
      throw new Error("File is not writable.");

    const name = target.path.split("/").pop()!;
    const directory =
      target.path.slice(0, target.path.lastIndexOf("/") + 1) || "/";
    const result = await dependencies.uploadFile(
      target.url,
      target.key,
      directory,
      new File([content], name, { type: "text/plain" }),
      target.chatId ?? undefined,
    );
    if (!result)
      throw new Error(
        "Failed to save file. Your changes are still in the editor.",
      );
    if (dependencies.getConnection() === target) dependencies.onSaved(content);
    await dependencies.nextTick();
  };

  return { save };
};
