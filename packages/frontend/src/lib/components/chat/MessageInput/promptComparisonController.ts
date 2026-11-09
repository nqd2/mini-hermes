export type PromptComparisonInput = {
  setText: (text: string) => void;
};

export type PromptComparisonContext = {
  chatId: string;
  input: PromptComparisonInput | null;
  prompt: string;
};

export type PromptComparisonTarget = {
  chatId: string;
  input: PromptComparisonInput;
  content: string;
};

export type PromptComparisonControllerDependencies<TModal> = {
  getCurrentContext: () => PromptComparisonContext;
  loadModal: () => Promise<TModal>;
  onLoadingChange: (loading: boolean) => void;
  onOpen: (state: { modal: TModal; target: PromptComparisonTarget }) => void;
  onError: (error: unknown) => void;
  onApplied: () => void;
  getStaleTargetError: () => Error;
};

export const createPromptComparisonController = <TModal>(
  dependencies: PromptComparisonControllerDependencies<TModal>,
) => {
  let loading = false;
  let target: PromptComparisonTarget | null = null;

  const open = async (): Promise<void> => {
    if (loading) return;
    loading = true;
    dependencies.onLoadingChange(true);

    const initialContext = dependencies.getCurrentContext();
    try {
      const modal = await dependencies.loadModal();
      const currentContext = dependencies.getCurrentContext();
      if (
        !initialContext.input ||
        initialContext.chatId !== currentContext.chatId ||
        initialContext.input !== currentContext.input
      ) {
        return;
      }

      target = {
        chatId: currentContext.chatId,
        input: currentContext.input,
        content: currentContext.prompt,
      };
      dependencies.onOpen({ modal, target });
    } catch (error) {
      dependencies.onError(error);
    } finally {
      loading = false;
      dependencies.onLoadingChange(false);
    }
  };

  const apply = (text: string): void => {
    const currentContext = dependencies.getCurrentContext();
    if (
      !currentContext.input ||
      target?.chatId !== currentContext.chatId ||
      target.input !== currentContext.input ||
      target.content !== currentContext.prompt
    ) {
      throw dependencies.getStaleTargetError();
    }

    if (text !== target.content) target.input.setText(text);
    dependencies.onApplied();
  };

  return { open, apply };
};
