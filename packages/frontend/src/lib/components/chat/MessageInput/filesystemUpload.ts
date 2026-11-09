export type FilesystemChatTerminal = {
  id?: string;
  url: string;
  key: string;
  enabled?: boolean;
  config?: { chat_uploads?: string; [key: string]: any };
  contexts?: { chat?: { context_id?: string; [key: string]: any } };
  [key: string]: any;
};

export const findFilesystemChatUploadTerminal = (
  selectedId: string | null | undefined,
  servers: FilesystemChatTerminal[] | null | undefined,
  settingsValue:
    { terminalServers?: FilesystemChatTerminal[] } | null | undefined,
): FilesystemChatTerminal | null => {
  if (!selectedId) return null;

  const systemTerminal = (servers ?? []).find(
    (terminal) =>
      terminal.id &&
      terminal.id === selectedId &&
      terminal.config?.chat_uploads === 'filesystem',
  );
  if (systemTerminal) return systemTerminal;

  return (
    settingsValue?.terminalServers?.find(
      (terminal) =>
        terminal.url === selectedId &&
        terminal.enabled &&
        terminal.config?.chat_uploads === 'filesystem',
    ) ?? null
  );
};

export type FilesystemChatUploadDependencies = {
  findTerminal: (
    terminalId: string | null | undefined,
  ) => FilesystemChatTerminal | null;
  getContextDirectory: (
    terminalId: string | null | undefined,
  ) => string | null | undefined;
  getCwd: (
    url: string,
    key: string,
    sessionId?: string,
  ) => Promise<{ cwd?: string } | null | undefined>;
  uploadFile: (
    url: string,
    key: string,
    cwd: string,
    file: File,
    sessionId?: string,
  ) => Promise<{ path: string; size?: number } | null | undefined>;
  isSavedChat: (sessionId: string | null) => boolean;
  showFileNavDirectory: (path: string) => void;
  touchAttachments: () => void;
  translate: (message: string) => string;
  showError: (message: string) => void;
};

/** Uploads one filesystem-backed composer attachment into its owning chat scope. */
export const uploadFilesystemChatAttachment = async (
  fileItem: Record<string, any>,
  sessionId: string | null,
  dependencies: FilesystemChatUploadDependencies,
): Promise<boolean> => {
  fileItem.status = 'uploading';
  fileItem.error = '';
  dependencies.touchAttachments();
  try {
    const terminal = dependencies.findTerminal(fileItem.terminal_id);
    if (!terminal)
      throw new Error('The attached file’s terminal is unavailable.');
    if (fileItem.session_id && fileItem.session_id !== sessionId)
      throw new Error('Attached files must belong to this chat.');
    if (
      terminal.contexts?.chat?.context_id === 'chat_id' &&
      !dependencies.isSavedChat(sessionId)
    ) {
      throw new Error('A saved chat is required for this terminal');
    }

    const cwd =
      dependencies.getContextDirectory(fileItem.terminal_id) ||
      (
        await dependencies.getCwd(
          terminal.url,
          terminal.key,
          sessionId || undefined,
        )
      )?.cwd;
    if (!cwd)
      throw new Error(
        dependencies.translate('Could not determine the upload folder.'),
      );
    const uploadedFile = await dependencies.uploadFile(
      terminal.url,
      terminal.key,
      cwd,
      fileItem.file,
      sessionId || undefined,
    );
    if (!uploadedFile)
      throw new Error(dependencies.translate('Failed to upload file.'));

    fileItem.status = 'uploaded';
    fileItem.session_id = sessionId;
    fileItem.name = uploadedFile.path.split('/').pop() || fileItem.name;
    fileItem.id = uploadedFile.path;
    fileItem.path = uploadedFile.path;
    fileItem.url = uploadedFile.path;
    fileItem.size = uploadedFile.size ?? fileItem.size;
    fileItem.file = uploadedFile;
    dependencies.showFileNavDirectory(uploadedFile.path);
  } catch (error) {
    fileItem.status = 'error';
    fileItem.error = `${error}`;
    dependencies.showError(fileItem.error);
    return false;
  } finally {
    dependencies.touchAttachments();
  }
  return true;
};
