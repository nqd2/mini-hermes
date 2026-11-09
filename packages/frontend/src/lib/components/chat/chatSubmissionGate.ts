export type ChatSubmissionGateOptions = {
  pendingOAuthIntegration: boolean;
  prompt: string;
  currentFileCount: number;
  hasUnselectedModel: boolean;
  getVariables: () => { conflicts: number; missing: boolean; empty: boolean };
  maximumFileCount: number | null;
  chatFileCount: number;
  needsWebSearchConfirmation: boolean;
};

export type ChatSubmissionGateResult =
  | { status: "ready" }
  | {
      status: "rejected";
      reason:
        | "integration-required"
        | "prompt-required"
        | "model-not-selected"
        | "chat-variables-conflict";
    }
  | { status: "rejected"; reason: "file-limit"; maximum: number }
  | {
      status: "deferred";
      reason: "chat-variables-input" | "web-search-confirmation";
    };

export const evaluateChatSubmission = (
  options: ChatSubmissionGateOptions,
): ChatSubmissionGateResult => {
  if (options.pendingOAuthIntegration) {
    return { status: "rejected", reason: "integration-required" };
  }
  if (options.prompt === "" && options.currentFileCount === 0) {
    return { status: "rejected", reason: "prompt-required" };
  }
  if (options.hasUnselectedModel) {
    return { status: "rejected", reason: "model-not-selected" };
  }

  const variables = options.getVariables();
  if (variables.conflicts > 0) {
    return { status: "rejected", reason: "chat-variables-conflict" };
  }
  if (variables.missing || variables.empty) {
    return { status: "deferred", reason: "chat-variables-input" };
  }
  if (
    options.maximumFileCount !== null &&
    options.currentFileCount + options.chatFileCount > options.maximumFileCount
  ) {
    return {
      status: "rejected",
      reason: "file-limit",
      maximum: options.maximumFileCount,
    };
  }
  if (options.needsWebSearchConfirmation) {
    return { status: "deferred", reason: "web-search-confirmation" };
  }
  return { status: "ready" };
};
