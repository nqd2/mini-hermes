import type { FilesystemFile } from "$lib/apis/terminal";

export type ChatFileAskRequest = {
  id: string;
  prompt: string;
  files: FilesystemFile[];
};

export type ChatFileAskControllerDependencies = {
  getLastMessage: () => { role: string; done?: boolean } | null;
  createRequestId: () => string;
  cloneFile: (file: FilesystemFile) => FilesystemFile;
  enqueueRequest: (request: ChatFileAskRequest) => void;
  submitPrompt: (prompt: string, files: FilesystemFile[]) => Promise<unknown>;
};

/** Preserves file attachments when a file-pane request is sent or queued. */
export const createChatFileAskController = (
  dependencies: ChatFileAskControllerDependencies,
) => {
  const askFile = async (
    instruction: string,
    file: FilesystemFile,
  ): Promise<"queued" | "submitted"> => {
    const attachments = [dependencies.cloneFile(file)];
    const lastMessage = dependencies.getLastMessage();
    if (lastMessage?.role === "assistant" && !lastMessage.done) {
      dependencies.enqueueRequest({
        id: dependencies.createRequestId(),
        prompt: instruction,
        files: attachments,
      });
      return "queued";
    }

    await dependencies.submitPrompt(instruction, attachments);
    return "submitted";
  };

  return { askFile };
};
