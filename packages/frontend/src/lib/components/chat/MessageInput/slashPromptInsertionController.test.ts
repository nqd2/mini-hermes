import { describe, expect, it, vi } from 'vitest';
import { createSlashPromptInsertionController } from './slashPromptInsertionController';

const makeEditor = () => {
  const transaction = {
    replaceWith: vi.fn(),
    setSelection: vi.fn(),
  };
  transaction.replaceWith.mockReturnValue(transaction);
  transaction.setSelection.mockReturnValue(transaction);

  const state = {
    tr: transaction,
    schema: {
      text: vi.fn((value: string | []) => ({
        type: { name: 'text' },
        text: String(value),
        nodeSize: String(value).length,
      })),
      nodes: {
        paragraph: {
          create: vi.fn(
            (_attributes: Record<string, unknown>, content?: any) => ({
              type: { name: 'paragraph' },
              content: { content: content ? [content] : [] },
              nodeSize: (content?.nodeSize ?? 0) + 2,
            }),
          ),
        },
      },
    },
    doc: { resolve: vi.fn((pos: number) => ({ pos })) },
    selection: {
      constructor: { near: vi.fn((resolved: unknown) => ({ resolved })) },
    },
  };
  const view = { dispatch: vi.fn() };
  return { state, view, transaction };
};

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  resolveTextVariables: vi.fn(async (text: string) => text),
  getRichTextMode: vi.fn(() => false),
  renderMarkdown: vi.fn((text: string) => text),
  parseRichText: vi.fn(),
  createRichTextSelection: vi.fn(() => ({ kind: 'rich-selection' })),
  createMultilineSelection: vi.fn(() => ({ kind: 'multiline-selection' })),
  createSingleLineSelection: vi.fn(() => ({ kind: 'single-selection' })),
  tick: vi.fn(async () => {}),
  resolveInputVariables: vi.fn(async (text: string) => text),
  focus: vi.fn(),
  ...overrides,
});

describe('createSlashPromptInsertionController', () => {
  it('replaces single-line prompt text and restores the nearby selection', async () => {
    const editor = makeEditor();
    const dependencies = makeDependencies();
    const controller = createSlashPromptInsertionController(dependencies);

    await controller.replaceSlashRangeWithPrompt(
      editor,
      { from: 4, to: 9 },
      'hello',
    );

    const insertedNode = editor.state.schema.text.mock.results[0].value;
    expect(dependencies.resolveTextVariables).toHaveBeenCalledWith('hello');
    expect(editor.transaction.replaceWith).toHaveBeenCalledWith(
      4,
      9,
      insertedNode,
    );
    expect(dependencies.createSingleLineSelection).toHaveBeenCalledWith(
      editor.state,
      10,
    );
    expect(editor.view.dispatch).toHaveBeenCalledWith(editor.transaction);
    expect(dependencies.resolveInputVariables).toHaveBeenCalledWith('hello');
    expect(dependencies.tick).toHaveBeenCalledTimes(2);
    expect(dependencies.focus).toHaveBeenCalledOnce();
  });

  it('inserts multiline prompt text as paragraph nodes and places the selection after it', async () => {
    const editor = makeEditor();
    const dependencies = makeDependencies();
    const controller = createSlashPromptInsertionController(dependencies);

    await controller.replaceSlashRangeWithPrompt(
      editor,
      { from: 3, to: 5 },
      'alpha\nbeta',
    );

    const insertedNodes = editor.transaction.replaceWith.mock.calls[0][2];
    expect(insertedNodes).toHaveLength(2);
    expect(editor.state.schema.nodes.paragraph.create).toHaveBeenCalledOnce();
    expect(dependencies.createMultilineSelection).toHaveBeenCalledWith(
      editor.state,
      14,
    );
    expect(editor.transaction.setSelection).toHaveBeenCalledWith({
      kind: 'multiline-selection',
    });
  });

  it('flattens rich-text paragraphs while retaining other block nodes', async () => {
    const editor = makeEditor();
    const textNode = { type: { name: 'text' }, nodeSize: 2 };
    const mentionNode = { type: { name: 'mention' }, nodeSize: 3 };
    const blockNode = { type: { name: 'heading' }, nodeSize: 5 };
    const dependencies = makeDependencies({
      getRichTextMode: vi.fn(() => true),
      parseRichText: vi.fn(() => ({
        content: {
          forEach: (callback: (node: any) => void) => {
            callback({
              type: { name: 'paragraph' },
              content: { content: [textNode, mentionNode] },
            });
            callback(blockNode);
          },
        },
      })),
    });
    const controller = createSlashPromptInsertionController(dependencies);

    await controller.replaceSlashRangeWithPrompt(
      editor,
      { from: 10, to: 12 },
      '**hello**',
    );

    expect(dependencies.renderMarkdown).toHaveBeenCalledWith('**hello**');
    expect(dependencies.parseRichText).toHaveBeenCalledWith(
      '**hello**',
      editor.state.schema,
    );
    expect(editor.transaction.replaceWith).toHaveBeenCalledWith(10, 12, [
      textNode,
      mentionNode,
      blockNode,
    ]);
    expect(dependencies.createRichTextSelection).toHaveBeenCalledWith(
      editor.state,
      20,
    );
  });
});
