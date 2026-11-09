export type ChatCommandDependencies = {
  clearInput: () => void;
  compact: () => unknown;
  showStatus: () => unknown;
  openComments: () => unknown;
  isSideChatAllowed: () => boolean;
  canStartSideChat: () => boolean;
  hasSingleModelSelected: () => boolean;
  getParentChatId: () => string;
  openSideChat: (parentChatId: string, prompt: string) => void;
  fork: () => unknown;
  switchModel: (modelId: string) => void;
  notifyError: (message: string) => void;
  translate: (key: string) => string;
};

/** Dispatches chat slash commands while leaving ordinary prompts to submission. */
export const createChatCommandController = (
  dependencies: ChatCommandDependencies,
) => ({
  handle(input: unknown): boolean | Promise<boolean> {
    const command = String(input).trim();

    if (command === "/compact") {
      dependencies.clearInput();
      return Promise.resolve(dependencies.compact()).then(() => true);
    }
    if (command === "/status") {
      dependencies.clearInput();
      dependencies.showStatus();
      return true;
    }
    if (command === "/comments") {
      dependencies.clearInput();
      dependencies.openComments();
      return true;
    }

    const sideCommand = command.match(/^\/(?:side|btw)(?:\s+([\s\S]*))?$/i);
    if (sideCommand && !dependencies.isSideChatAllowed()) {
      dependencies.notifyError(
        dependencies.translate("You do not have permission to use side chat."),
      );
      return true;
    }
    if (sideCommand && dependencies.canStartSideChat()) {
      if (!dependencies.hasSingleModelSelected()) {
        dependencies.notifyError(
          dependencies.translate("Select only one model to open a side chat."),
        );
        return true;
      }
      dependencies.clearInput();
      dependencies.openSideChat(
        dependencies.getParentChatId(),
        sideCommand[1] ?? "",
      );
      return true;
    }

    if (command === "/fork") {
      dependencies.clearInput();
      return Promise.resolve(dependencies.fork()).then(() => true);
    }

    const modelCommand = command.match(/^\/model(?:\s+([\s\S]+))?$/);
    if (modelCommand) {
      dependencies.switchModel(modelCommand[1]?.trim() ?? "");
      return true;
    }

    return false;
  },
});
