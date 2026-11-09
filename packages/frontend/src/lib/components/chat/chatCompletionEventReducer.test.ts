import { describe, expect, it, vi } from 'vitest';
import { applyChatCompletionEvent } from './chatCompletionEventReducer';

const makeDependencies = () => ({
  getOutputText: vi.fn((output: any[]) =>
    output.map((item) => item.text ?? '').join(''),
  ),
  handleError: vi.fn((error, message) => {
    message.error = error;
  }),
  dispatchAudio: vi.fn(),
  vibrate: vi.fn(),
  captureUsageCheckpoint: vi.fn(),
  logEmptyResponse: vi.fn(),
});

describe('applyChatCompletionEvent', () => {
  it('applies output, sources, selected model and usage while preserving UI effects', () => {
    const message: Record<string, any> = { id: 'message-1', content: '' };
    const dependencies = makeDependencies();
    const output = [{ type: 'message', text: 'answer' }];
    const usage = { total_tokens: 12 };

    const result = applyChatCompletionEvent(
      {
        type: 'response.output_text.delta',
        output,
        sources: [{ name: 'source' }],
        selected_model_id: 'routed-model',
        usage,
        done: true,
      },
      message,
      false,
      { ...dependencies, hapticFeedback: true },
    );

    expect(result.done).toBe(true);
    expect(message).toMatchObject({
      output,
      content: 'answer',
      sources: [{ name: 'source' }],
      selectedModelId: 'routed-model',
      arena: true,
      usage,
    });
    expect(dependencies.vibrate).toHaveBeenCalledWith(5);
    expect(dependencies.dispatchAudio).toHaveBeenCalledWith(message);
    expect(dependencies.captureUsageCheckpoint).toHaveBeenCalledWith(message);
  });

  it('appends streamed text and skips effects for an empty newline response', () => {
    const message: Record<string, any> = { content: '' };
    const dependencies = makeDependencies();

    applyChatCompletionEvent(
      { choices: [{ delta: { content: '\n' } }] },
      message,
      false,
      { ...dependencies, hapticFeedback: true },
    );

    expect(message.content).toBe('');
    expect(dependencies.logEmptyResponse).toHaveBeenCalledOnce();
    expect(dependencies.vibrate).not.toHaveBeenCalled();
    expect(dependencies.dispatchAudio).not.toHaveBeenCalled();

    applyChatCompletionEvent(
      { choices: [{ delta: { content: 'hello' } }] },
      message,
      false,
      { ...dependencies, hapticFeedback: true },
    );
    expect(message.content).toBe('hello');
    expect(dependencies.vibrate).toHaveBeenCalledWith(5);
    expect(dependencies.dispatchAudio).toHaveBeenCalledWith(message);
  });

  it('appends non-stream choice content and replaces realtime content', () => {
    const message: Record<string, any> = { content: 'old ' };
    const dependencies = makeDependencies();

    applyChatCompletionEvent(
      { choices: [{ message: { content: 'answer' } }] },
      message,
      false,
      { ...dependencies, hapticFeedback: false },
    );
    expect(message.content).toBe('old answer');
    expect(dependencies.dispatchAudio).toHaveBeenCalledOnce();
    expect(dependencies.vibrate).not.toHaveBeenCalled();

    applyChatCompletionEvent({ content: 'realtime' }, message, false, {
      ...dependencies,
      hapticFeedback: true,
    });
    expect(message.content).toBe('realtime');
    expect(dependencies.dispatchAudio).toHaveBeenCalledTimes(2);
    expect(dependencies.vibrate).toHaveBeenCalledWith(5);
  });

  it('updates shared messages without local audio or haptic effects', () => {
    const message: Record<string, any> = { content: '' };
    const dependencies = makeDependencies();

    applyChatCompletionEvent(
      { content: 'shared answer', error: { message: 'provider error' } },
      message,
      true,
      { ...dependencies, hapticFeedback: true },
    );

    expect(message.content).toBe('shared answer');
    expect(message.error).toEqual({ message: 'provider error' });
    expect(dependencies.handleError).not.toHaveBeenCalled();
    expect(dependencies.dispatchAudio).not.toHaveBeenCalled();
    expect(dependencies.vibrate).not.toHaveBeenCalled();
  });
});
