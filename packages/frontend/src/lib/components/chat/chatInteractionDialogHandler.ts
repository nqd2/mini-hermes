export type ChatInteractionDialogData = Record<string, any> | null | undefined;
export type ChatInteractionCallback = ((value: any) => void) | null | undefined;

export type ChatInteractionDialogDependencies = {
  showConfirmation: (
    callback: ChatInteractionCallback,
    data: ChatInteractionDialogData,
  ) => void;
  showInput: (
    callback: ChatInteractionCallback,
    data: ChatInteractionDialogData,
  ) => void;
  executeCode: (code: unknown) => Promise<unknown>;
  logExecutionError: (error: unknown) => void;
};

export type ChatInteractionDialogResult = boolean | Promise<boolean>;

/** Handles chat events that open an interaction dialog or execute a tool script. */
export const handleChatInteractionDialogEvent = (
  type: string | null,
  data: ChatInteractionDialogData,
  callback: ChatInteractionCallback,
  dependencies: ChatInteractionDialogDependencies,
): ChatInteractionDialogResult => {
  if (type === "confirmation") {
    dependencies.showConfirmation(callback, data);
    return true;
  }

  if (type === "input") {
    dependencies.showInput(callback, data);
    return true;
  }

  if (type === "execute") {
    return (async () => {
      try {
        const result = await dependencies.executeCode(data!.code);
        callback?.(result);
      } catch (error) {
        dependencies.logExecutionError(error);
      }
      return true;
    })();
  }

  return false;
};
