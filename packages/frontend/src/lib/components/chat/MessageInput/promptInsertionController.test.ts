import { describe, expect, it, vi } from 'vitest';
import { createPromptInsertionController } from './promptInsertionController';

const makeInput = () => ({
  scrollTop: 0,
  scrollHeight: 120,
  dispatchEvent: vi.fn(() => true),
});

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  getInputElement: vi.fn(() => makeInput()),
  resolveTextVariables: vi.fn(async (text: string) => `resolved:${text}`),
  getCommand: vi.fn(() => ''),
  replaceCommandWithText: vi.fn(),
  insertContent: vi.fn(),
  tick: vi.fn(async () => {}),
  resolveInputVariables: vi.fn(async (text: string) => text),
  getInputContainer: vi.fn(() => ({ scrollTop: 0, scrollHeight: 300 })),
  focus: vi.fn(),
  getPrompt: vi.fn(() => 'prompt'),
  extractCurlyBraceWords: vi.fn(() => []),
  ...overrides,
});

describe('createPromptInsertionController', () => {
  it('does nothing when the composer element is missing', async () => {
    const dependencies = makeDependencies({
      getInputElement: vi.fn(() => null),
    });
    const controller = createPromptInsertionController(dependencies);

    await controller.insertTextAtCursor('hello');

    expect(dependencies.resolveTextVariables).not.toHaveBeenCalled();
    expect(dependencies.insertContent).not.toHaveBeenCalled();
    expect(dependencies.tick).not.toHaveBeenCalled();
  });

  it('resolves text, replaces the active command, and preserves modal/tick ordering', async () => {
    const input = makeInput();
    const dependencies = makeDependencies({
      getInputElement: vi.fn(() => input),
      getCommand: vi.fn(() => '/prompt'),
      extractCurlyBraceWords: vi.fn(() => ['{{NAME}}']),
    });
    const controller = createPromptInsertionController(dependencies);

    await controller.insertTextAtCursor('hello');

    expect(dependencies.resolveTextVariables).toHaveBeenCalledWith('hello');
    expect(dependencies.replaceCommandWithText).toHaveBeenCalledWith(
      'resolved:hello',
    );
    expect(dependencies.insertContent).not.toHaveBeenCalled();
    expect(dependencies.resolveInputVariables).toHaveBeenCalledWith(
      'resolved:hello',
    );
    expect(dependencies.focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(input.dispatchEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'input' }),
    );
    expect(dependencies.tick).toHaveBeenCalledTimes(4);
    expect(input.scrollTop).toBe(0);
  });

  it('inserts normal text and scrolls both composer containers when no variable modal opens', async () => {
    const input = makeInput();
    const container = { scrollTop: 0, scrollHeight: 300 };
    const dependencies = makeDependencies({
      getInputElement: vi.fn(() => input),
      getInputContainer: vi.fn(() => container),
    });
    const controller = createPromptInsertionController(dependencies);

    await controller.insertTextAtCursor('hello');

    expect(dependencies.insertContent).toHaveBeenCalledWith('resolved:hello');
    expect(dependencies.replaceCommandWithText).not.toHaveBeenCalled();
    expect(container.scrollTop).toBe(300);
    expect(input.scrollTop).toBe(120);
    expect(dependencies.tick).toHaveBeenCalledTimes(3);
  });
});
