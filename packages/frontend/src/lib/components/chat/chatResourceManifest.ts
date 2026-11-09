export type ChatResourceManifestFile = {
  id?: string;
  type?: string;
  content_type?: string;
  [key: string]: any;
};

export type ChatResourceManifestDependencies = {
  chatFiles: ChatResourceManifestFile[];
  messages: Array<{ files?: ChatResourceManifestFile[] | null }>;
  userFiles: ChatResourceManifestFile[];
  cloneTrackedFiles: (
    files: ChatResourceManifestFile[],
  ) => ChatResourceManifestFile[];
  isRasterImageContentType: (contentType?: string) => boolean;
  equal: (
    left: ChatResourceManifestFile,
    right: ChatResourceManifestFile,
  ) => boolean;
};

/** Builds model-visible file references from retained chat scope and the current user turn. */
export const buildChatRequestFiles = ({
  chatFiles,
  messages,
  userFiles,
  cloneTrackedFiles,
  isRasterImageContentType,
  equal,
}: ChatResourceManifestDependencies) => {
  const referencedMessageFiles = messages
    .filter((message) => message.files)
    .flatMap((message) => message.files ?? []);
  const retainedChatFiles = chatFiles.filter((item) =>
    referencedMessageFiles.some((messageFile) => messageFile.id === item.id),
  );
  const files = cloneTrackedFiles(retainedChatFiles);
  files.push(
    ...userFiles.filter(
      (item) =>
        ["doc", "text", "note", "chat", "collection", "folder"].includes(
          item.type ?? "",
        ) ||
        (item.type === "file" && !isRasterImageContentType(item.content_type)),
    ),
  );

  return {
    chatFiles: retainedChatFiles,
    files: files.filter(
      (item, index, allFiles) =>
        allFiles.findIndex((candidate) => equal(candidate, item)) === index,
    ),
  };
};
