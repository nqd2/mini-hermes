export type ChatInputClipboardItem = Pick<
  DataTransferItem,
  "type" | "getAsFile"
>;

export type ChatInputClipboardData = {
  items?: ArrayLike<ChatInputClipboardItem>;
  getData: (format: string) => string;
};

export type ChatInputPasteEvent = {
  clipboardData?: ChatInputClipboardData | null;
  preventDefault: () => void;
};

export type ChatInputPasteControllerDependencies = {
  isLargeTextAsFileEnabled: () => boolean;
  isShiftPressed: () => boolean;
  maximumTextLength: number;
  createTextFile: (text: string) => File;
  uploadTextFile: (file: File) => Promise<unknown>;
  processInputFiles: (files: File[]) => Promise<unknown>;
};

/** Routes pasted files and oversized text through the existing composer upload flows. */
export const createChatInputPasteController = ({
  isLargeTextAsFileEnabled,
  isShiftPressed,
  maximumTextLength,
  createTextFile,
  uploadTextFile,
  processInputFiles,
}: ChatInputPasteControllerDependencies) => {
  const onPaste = async (event: ChatInputPasteEvent): Promise<void> => {
    const clipboardData = event.clipboardData;
    if (!clipboardData?.items) return;

    for (const item of Array.from(clipboardData.items)) {
      if (item.type === "text/plain") {
        if (!isLargeTextAsFileEnabled() || isShiftPressed()) continue;

        const text = clipboardData.getData("text/plain");
        if (text.length <= maximumTextLength) continue;

        event.preventDefault();
        await uploadTextFile(createTextFile(text));
        continue;
      }

      const file = item.getAsFile();
      if (!file) continue;

      await processInputFiles([file]);
      event.preventDefault();
    }
  };

  return { onPaste };
};
