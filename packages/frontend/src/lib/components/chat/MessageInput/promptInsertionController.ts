type PromptInsertionElement = Pick<
  HTMLElement,
  'scrollTop' | 'scrollHeight' | 'dispatchEvent'
>;

type PromptInsertionContainer = Pick<HTMLElement, 'scrollTop' | 'scrollHeight'>;

export type PromptInsertionControllerDependencies = {
  getInputElement: () => PromptInsertionElement | null;
  resolveTextVariables: (text: string) => Promise<string>;
  getCommand: () => string;
  replaceCommandWithText: (text: string) => void;
  insertContent: (text: string) => void;
  tick: () => Promise<void>;
  resolveInputVariables: (text: string) => Promise<string>;
  getInputContainer: () => PromptInsertionContainer | null;
  focus: (options?: FocusOptions) => void;
  getPrompt: () => string;
  extractCurlyBraceWords: (text: string) => string[];
};

export const createPromptInsertionController = (
  dependencies: PromptInsertionControllerDependencies,
) => {
  const insertTextAtCursor = async (initialText: string): Promise<void> => {
    const input = dependencies.getInputElement();
    if (!input) return;

    const text = await dependencies.resolveTextVariables(initialText);
    if (dependencies.getCommand()) {
      dependencies.replaceCommandWithText(text);
    } else {
      dependencies.insertContent(text);
    }

    await dependencies.tick();
    await dependencies.resolveInputVariables(text);
    await dependencies.tick();

    const container = dependencies.getInputContainer();
    if (container) {
      container.scrollTop = container.scrollHeight;
    }

    await dependencies.tick();
    dependencies.focus({ preventScroll: true });
    input.dispatchEvent(new Event('input'));

    if (
      dependencies.extractCurlyBraceWords(dependencies.getPrompt()).length > 0
    ) {
      await dependencies.tick();
    } else {
      input.scrollTop = input.scrollHeight;
    }
  };

  return { insertTextAtCursor };
};
