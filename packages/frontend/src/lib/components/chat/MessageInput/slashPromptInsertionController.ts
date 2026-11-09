type PromptEditor = {
  state: any;
  view: { dispatch: (transaction: any) => void };
};

type PromptRange = { from: number; to: number };

type PromptNode = {
  type: { name: string };
  content: { content: PromptNode[] };
  nodeSize: number;
};

type PromptFragment = {
  content: { forEach: (callback: (node: any) => void) => void };
};

export type SlashPromptInsertionDependencies = {
  resolveTextVariables: (text: string) => Promise<string>;
  getRichTextMode: () => boolean;
  renderMarkdown: (text: string) => string;
  parseRichText: (html: string, schema: any) => PromptFragment;
  createRichTextSelection: (state: any, position: number) => unknown;
  createMultilineSelection: (state: any, position: number) => unknown;
  createSingleLineSelection: (state: any, position: number) => unknown;
  tick: () => Promise<void>;
  resolveInputVariables: (text: string) => Promise<string>;
  focus: (options?: FocusOptions) => void;
};

export const createSlashPromptInsertionController = (
  dependencies: SlashPromptInsertionDependencies,
) => {
  const replaceSlashRangeWithPrompt = async (
    editor: PromptEditor,
    range: PromptRange,
    initialText: string,
  ): Promise<void> => {
    const text = await dependencies.resolveTextVariables(initialText);
    const { state, view } = editor;
    let transaction = state.tr;

    if (dependencies.getRichTextMode()) {
      const fragment = dependencies.parseRichText(
        dependencies.renderMarkdown(text),
        state.schema,
      );
      const nodesToInsert: PromptNode[] = [];
      fragment.content.forEach((node) => {
        if (node.type.name === 'paragraph') {
          nodesToInsert.push(...node.content.content);
        } else {
          nodesToInsert.push(node);
        }
      });

      transaction = transaction.replaceWith(
        range.from,
        range.to,
        nodesToInsert,
      );
      const newPosition =
        range.from +
        nodesToInsert.reduce((sum, node) => sum + node.nodeSize, 0);
      transaction = transaction.setSelection(
        dependencies.createRichTextSelection(state, newPosition),
      );
    } else if (text.includes('\n')) {
      const nodes = text
        .split('\n')
        .map((line, index) =>
          index === 0
            ? state.schema.text(line ? line : [])
            : state.schema.nodes.paragraph.create(
                {},
                line ? state.schema.text(line) : undefined,
              ),
        );

      transaction = transaction.replaceWith(range.from, range.to, nodes);
      const newPosition = nodes.reduce(
        (position, node) => position + node.nodeSize,
        range.from,
      );
      transaction = transaction.setSelection(
        dependencies.createMultilineSelection(state, newPosition),
      );
    } else {
      transaction = transaction.replaceWith(
        range.from,
        range.to,
        text !== '' ? state.schema.text(text) : [],
      );
      transaction = transaction.setSelection(
        dependencies.createSingleLineSelection(
          state,
          range.from + text.length + 1,
        ),
      );
    }

    view.dispatch(transaction);
    await dependencies.tick();
    await dependencies.resolveInputVariables(text);
    await dependencies.tick();
    dependencies.focus({ preventScroll: true });
  };

  return { replaceSlashRangeWithPrompt };
};
