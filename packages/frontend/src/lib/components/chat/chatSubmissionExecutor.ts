import {
  getChatSubmissionDispatchDecision,
  hasPendingChatFileTransfers,
} from "./chatSubmissionDispatch";
import { prepareChatSubmission } from "./chatSubmissionPreparation";

type BridgeRequest = { userMessageId: string; modelId: string } | null;
type SubmissionResult = { status: "submitted" | "deferred" | "rejected" };

export type ChatSubmissionExecutorOptions<
  THistory,
  TFile extends { status?: string },
  TResult extends SubmissionResult = SubmissionResult,
> = {
  prompt: string;
  history: THistory;
  bridgeRequest: BridgeRequest;
  isCurrentHistory: (history: THistory) => boolean;
  resolveChatId: (history: THistory) => Promise<string | null>;
  ensureChatId: (history: THistory) => Promise<string>;
  hasPendingFiles: () => boolean;
  uploadPendingFiles: (sessionId: string) => Promise<boolean>;
  setProcessing: (state: "uploading" | "") => void;
  notifyPreparationError: (error: unknown) => void;
  getQueuedMessages: () => readonly { files?: readonly TFile[] | null }[];
  getCurrentFiles: () => TFile[];
  isAssistantGenerating: () => boolean;
  isMessageQueueEnabled: () => boolean;
  createRequestId: () => string;
  enqueueRequest: (request: {
    id: string;
    prompt: string;
    files: TFile[];
  }) => void;
  cloneFiles: (files: TFile[]) => TFile[];
  clearQueuedInput: () => void;
  stopResponse: () => Promise<unknown>;
  waitForRender: () => Promise<unknown>;
  prepareFinalInput: () => TFile[];
  submit: (
    prompt: string,
    files: TFile[],
    bridgeRequest: BridgeRequest,
  ) => Promise<TResult | null | undefined>;
};

/** Coordinates chat preparation, queue policy, input ownership and the final send adapter. */
export const executeChatSubmission = async <
  THistory,
  TFile extends { status?: string },
  TResult extends SubmissionResult = SubmissionResult,
>(
  options: ChatSubmissionExecutorOptions<THistory, TFile, TResult>,
): Promise<TResult | { status: "rejected" | "deferred" }> => {
  options.setProcessing("uploading");
  try {
    const preparation = await prepareChatSubmission({
      history: options.history,
      isCurrentHistory: options.isCurrentHistory,
      resolveChatId: options.resolveChatId,
      ensureChatId: options.ensureChatId,
      hasPendingFiles: options.hasPendingFiles,
      uploadPendingFiles: options.uploadPendingFiles,
    });
    if (preparation.status !== "ready") return { status: "rejected" };
  } catch (error) {
    options.notifyPreparationError(error);
    return { status: "rejected" };
  } finally {
    options.setProcessing("");
  }

  const currentFiles = options.getCurrentFiles();
  const pendingFileTransfers = hasPendingChatFileTransfers(
    options.getQueuedMessages(),
    currentFiles,
  );
  const assistantGenerating = pendingFileTransfers
    ? false
    : options.isAssistantGenerating();
  const dispatch = getChatSubmissionDispatchDecision({
    isBridgeRequest: Boolean(options.bridgeRequest),
    hasPendingFileTransfers: pendingFileTransfers,
    assistantGenerating,
    messageQueueEnabled: options.isMessageQueueEnabled(),
  });

  if (dispatch.action === "defer") return { status: "deferred" };
  if (dispatch.action === "queue") {
    options.enqueueRequest({
      id: options.createRequestId(),
      prompt: options.prompt,
      files: dispatch.snapshotFiles
        ? options.cloneFiles(currentFiles)
        : currentFiles,
    });
    options.clearQueuedInput();
    return { status: "rejected" };
  }
  if (dispatch.action === "interrupt") {
    await options.stopResponse();
    await options.waitForRender();
  }

  const files = options.prepareFinalInput();
  return (
    (await options.submit(options.prompt, files, options.bridgeRequest)) ?? {
      status: "rejected",
    }
  );
};
