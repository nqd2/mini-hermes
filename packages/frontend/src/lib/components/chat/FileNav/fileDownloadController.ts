export type FileDownloadResult = { blob: Blob; filename: string } | null;
export type FileDownloadTerminal = { url: string; key: string };

export type FileDownloadControllerDependencies<ToastId> = {
  getTerminal: () => FileDownloadTerminal | null;
  getSessionId: () => string | undefined;
  downloadFile: (
    url: string,
    key: string,
    path: string,
    sessionId?: string,
  ) => Promise<FileDownloadResult>;
  archiveDirectory: (
    url: string,
    key: string,
    paths: string[],
    sessionId?: string,
  ) => Promise<FileDownloadResult>;
  setDownloading: (value: boolean) => void;
  showPreparingToast: () => ToastId;
  showDownloadFailed: () => void;
  dismissToast: (id: ToastId) => void;
  createObjectUrl: (blob: Blob) => string;
  clickDownload: (url: string, filename: string) => void;
  revokeObjectUrl: (url: string) => void;
};

/** Runs FileNav's file and directory download flow behind injectable I/O adapters. */
export const createFileDownloadController = <ToastId>(
  dependencies: FileDownloadControllerDependencies<ToastId>,
) => {
  let isDownloading = false;

  const download = async (path: string): Promise<void> => {
    const terminal = dependencies.getTerminal();
    if (!terminal || isDownloading) return;

    isDownloading = true;
    dependencies.setDownloading(true);
    let toastId: ToastId | undefined;
    try {
      toastId = dependencies.showPreparingToast();
      const sessionId = dependencies.getSessionId();
      const result = path.endsWith("/")
        ? await dependencies.archiveDirectory(
            terminal.url,
            terminal.key,
            [path.replace(/\/$/, "")],
            sessionId,
          )
        : await dependencies.downloadFile(
            terminal.url,
            terminal.key,
            path,
            sessionId,
          );

      if (!result) {
        dependencies.showDownloadFailed();
        return;
      }

      const url = dependencies.createObjectUrl(result.blob);
      dependencies.clickDownload(url, result.filename);
      dependencies.revokeObjectUrl(url);
    } finally {
      if (toastId !== undefined) dependencies.dismissToast(toastId);
      isDownloading = false;
      dependencies.setDownloading(false);
    }
  };

  return { download };
};
