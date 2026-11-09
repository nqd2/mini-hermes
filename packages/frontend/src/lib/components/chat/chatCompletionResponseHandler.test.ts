import { describe, expect, it, vi } from 'vitest';
import { handleChatCompletionResponse } from './chatCompletionResponseHandler';

const createInput = (overrides: Record<string, any> = {}) => ({
  response: { chat_id: 'chat-1' },
  responseMessageId: 'response-1',
  responseMessage: { id: 'response-1' },
  messageIdsList: undefined,
  messages: { 'response-1': { done: false } },
  onError: vi.fn(),
  onToolCallResolved: vi.fn(),
  ...overrides,
});

describe('handleChatCompletionResponse', () => {
  it('returns empty when transport has no response', async () => {
    const input = createInput({ response: undefined });

    await expect(handleChatCompletionResponse(input)).resolves.toBe('empty');
    expect(input.onError).not.toHaveBeenCalled();
    expect(input.onToolCallResolved).not.toHaveBeenCalled();
  });

  it('routes errors and skips successful completion callbacks', async () => {
    const response = { error: { message: 'provider error' } };
    const input = createInput({ response });

    await expect(handleChatCompletionResponse(input)).resolves.toBe('error');
    expect(input.onError).toHaveBeenCalledWith(response.error, input.responseMessage);
    expect(input.onToolCallResolved).not.toHaveBeenCalled();
  });

  it('resolves the provider response when any tracked message is still pending', async () => {
    const response = { chat_id: 'chat-1' };
    const input = createInput({
      response,
      messageIdsList: [
        { model_id: 'model-1', message_id: 'response-1' },
        { model_id: 'model-2', message_id: 'response-2' },
      ],
      messages: { 'response-1': { done: true }, 'response-2': { done: false } },
    });

    await expect(handleChatCompletionResponse(input)).resolves.toBe('success');
    expect(input.onToolCallResolved).toHaveBeenCalledWith(response);
  });

  it('does not resolve the provider response after all tracked messages are done', async () => {
    const input = createInput({
      messageIdsList: [{ model_id: 'model-1', message_id: 'response-1' }],
      messages: { 'response-1': { done: true } },
    });

    await expect(handleChatCompletionResponse(input)).resolves.toBe('success');
    expect(input.onToolCallResolved).not.toHaveBeenCalled();
  });
});
