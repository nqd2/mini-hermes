import type {
  FilePreviewData,
  FilePreviewTerminal,
  FilePreviewTranslator,
} from './filePreviewLoader';

export type FilePreviewRequestResult =
  | { status: 'stale' }
  | { status: 'loaded'; preview: FilePreviewData }
  | { status: 'failed'; error: unknown };

export type FilePreviewRequestDependencies = {
  path: string;
  terminal: FilePreviewTerminal;
  sessionId: string | null;
  translate: FilePreviewTranslator;
  isCurrent: () => boolean;
  loadPreview: (
    path: string,
    terminal: FilePreviewTerminal,
    sessionId: string | null,
    translate: FilePreviewTranslator,
  ) => Promise<FilePreviewData>;
  revokeObjectURL: (url: string) => void;
};

/** Discards stale preview results and releases any object URLs they allocated. */
export const loadCurrentFilePreviewRequest = async (
  dependencies: FilePreviewRequestDependencies,
): Promise<FilePreviewRequestResult> => {
  let preview: FilePreviewData;
  try {
    preview = await dependencies.loadPreview(
      dependencies.path,
      dependencies.terminal,
      dependencies.sessionId,
      dependencies.translate,
    );
  } catch (error) {
    return dependencies.isCurrent()
      ? { status: 'failed', error }
      : { status: 'stale' };
  }
  if (!dependencies.isCurrent()) {
    for (const url of [
      preview.fileImageUrl,
      preview.fileVideoUrl,
      preview.fileAudioUrl,
    ]) {
      if (url) dependencies.revokeObjectURL(url);
    }
    return { status: 'stale' };
  }
  return { status: 'loaded', preview };
};
