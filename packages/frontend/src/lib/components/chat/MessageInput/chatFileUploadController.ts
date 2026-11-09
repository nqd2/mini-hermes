export type ChatUploadAttachment = {
  type: string;
  file: unknown;
  id: string | null | undefined;
  url: string;
  name: string;
  collection_name: string | null | undefined;
  status: string;
  size: number;
  error: string;
  itemId: string;
  [key: string]: any;
};

type FilesystemTerminal = {
  contexts?: { chat?: { context_id?: string | null } | null } | null;
};

type UploadedChatFile = {
  id?: string;
  meta?: {
    collection_name?: string | null;
    content_type?: string | null;
  } | null;
  collection_name?: string | null;
  content_type?: string | null;
  error?: string | null;
};

export type ChatFileUploadDependencies = {
  canUpload: () => boolean;
  canUploadWithSelectedModels: () => boolean;
  getFilesystemTerminal: () => FilesystemTerminal | null | undefined;
  getSelectedTerminalId: () => string | null | undefined;
  getChatId: () => string | null | undefined;
  isTemporaryChat: () => boolean;
  getDefaultUploadContext: () => boolean;
  getToken: () => string;
  getAudioLanguage: () => string | null | undefined;
  createId: () => string;
  translate: (message: string, values?: Record<string, unknown>) => string;
  showError: (message: string) => void;
  showWarning: (message: string) => void;
  addAttachment: (attachment: ChatUploadAttachment) => void;
  touchAttachments: () => void;
  removeAttachment: (
    predicate: (attachment: ChatUploadAttachment) => boolean,
  ) => void;
  uploadFilesystem: (
    attachment: ChatUploadAttachment,
    sessionId: string | null,
  ) => Promise<boolean>;
  uploadToServer: (
    token: string,
    file: File,
    metadata: Record<string, unknown> | null,
    process: boolean,
  ) => Promise<UploadedChatFile | null | undefined>;
  extractContent: (file: File) => Promise<unknown>;
  onUpdate: (payload: { file: ChatUploadAttachment }) => void;
};

/** Owns upload policy and storage branches; UI state and notifications remain adapters. */
export const createChatFileUploadController = (
  dependencies: ChatFileUploadDependencies,
) => ({
  async upload(
    file: File,
    process = true,
    itemData: Record<string, unknown> = {},
  ): Promise<void | null> {
    if (!dependencies.canUpload()) {
      dependencies.showError(
        dependencies.translate("You do not have permission to upload files."),
      );
      return null;
    }

    const filesystemTerminal = dependencies.getFilesystemTerminal();
    if (!filesystemTerminal && !dependencies.canUploadWithSelectedModels()) {
      dependencies.showError(
        dependencies.translate("Model(s) do not support file upload"),
      );
      return null;
    }

    const itemId = dependencies.createId();
    const fileItem: ChatUploadAttachment = {
      type: "file",
      file: "",
      id: null,
      url: "",
      name: file.name,
      collection_name: "",
      status: "uploading",
      size: file.size,
      error: "",
      itemId,
      ...(dependencies.getDefaultUploadContext() ? { context: "full" } : {}),
      ...itemData,
    };

    if (fileItem.size === 0) {
      dependencies.showError(
        dependencies.translate("You cannot upload an empty file."),
      );
      return null;
    }

    dependencies.addAttachment(fileItem);

    if (filesystemTerminal) {
      fileItem.type = "filesystem";
      fileItem.file = file;
      fileItem.terminal_id = dependencies.getSelectedTerminalId();
      fileItem.session_id = dependencies.getChatId() || null;
      if (
        !fileItem.session_id &&
        filesystemTerminal.contexts?.chat?.context_id === "chat_id"
      ) {
        fileItem.status = "pending";
        dependencies.touchAttachments();
        return;
      }

      if (
        !(await dependencies.uploadFilesystem(fileItem, fileItem.session_id))
      ) {
        dependencies.removeAttachment((item) => item?.itemId === itemId);
      }
      dependencies.onUpdate({ file: fileItem });
      return;
    }

    if (!dependencies.isTemporaryChat()) {
      try {
        let metadata: Record<string, unknown> | null = null;
        if (
          (file.type.startsWith("audio/") || file.type.startsWith("video/")) &&
          dependencies.getAudioLanguage()
        ) {
          metadata = { language: dependencies.getAudioLanguage() };
        }
        if (/\.(docx|xlsx|pptx|pdf)$/i.test(file.name)) {
          metadata = { ...(metadata ?? {}), mini_hermes_chat_upload: true };
        }

        const uploadedFile = await dependencies.uploadToServer(
          dependencies.getToken(),
          file,
          metadata,
          process,
        );
        if (uploadedFile) {
          if (uploadedFile.error) dependencies.showWarning(uploadedFile.error);

          fileItem.status = "uploaded";
          fileItem.file = uploadedFile;
          fileItem.id = uploadedFile.id;
          fileItem.collection_name =
            uploadedFile.meta?.collection_name || uploadedFile.collection_name;
          fileItem.content_type =
            uploadedFile.meta?.content_type || uploadedFile.content_type;
          fileItem.url = `${uploadedFile.id}`;
          dependencies.touchAttachments();
        } else {
          fileItem.status = "error";
          fileItem.error = dependencies.translate("Failed to upload file.");
          dependencies.removeAttachment((item) => item?.itemId === itemId);
        }
      } catch (error) {
        fileItem.status = "error";
        fileItem.error = `${error}`;
        dependencies.showError(`${error}`);
        dependencies.removeAttachment((item) => item?.itemId === itemId);
      } finally {
        dependencies.onUpdate({ file: fileItem });
      }
      return;
    }

    const content = await dependencies.extractContent(file).catch((error) => {
      dependencies.showError(
        dependencies.translate(
          "Failed to extract content from the file: {{error}}",
          {
            error,
          },
        ),
      );
      return null;
    });

    if (content === null) {
      fileItem.status = "error";
      fileItem.error = dependencies.translate(
        "Failed to extract content from the file.",
      );
      dependencies.showError(
        dependencies.translate("Failed to extract content from the file."),
      );
      dependencies.removeAttachment((item) => item?.itemId === itemId);
      dependencies.onUpdate({ file: fileItem });
      return null;
    }

    fileItem.status = "uploaded";
    fileItem.type = "text";
    fileItem.content = content;
    fileItem.id = dependencies.createId();
    dependencies.touchAttachments();
    dependencies.onUpdate({ file: fileItem });
  },
});
