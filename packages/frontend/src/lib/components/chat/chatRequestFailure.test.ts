import { describe, expect, it, vi } from 'vitest';
import { applyChatRequestFailure, applyOpenAIResponseError } from './chatRequestFailure';

const createHarness = (error: any) => {
  const responseMessage = { id: 'response-1', done: false };
  const history = {
    currentId: 'user-1',
    messages: { 'response-1': responseMessage },
  };
  const translate = vi.fn((key: string) => `translated:${key}`);
  const notify = vi.fn();

  return {
    responseMessage,
    history,
    translate,
    notify,
    result: applyChatRequestFailure({
      error,
      responseMessage,
      responseMessageId: 'response-1',
      history,
      translate,
      notify,
    }),
  };
};

describe('applyChatRequestFailure', () => {
  it('shows a nested provider message and records the failed assistant response', () => {
    const error = { error: { message: 'provider is unavailable' } };
    const { history, notify, responseMessage, result } = createHarness(error);

    expect(result).toBeNull();
    expect(notify).toHaveBeenCalledWith('provider is unavailable');
    expect(responseMessage).toMatchObject({
      done: true,
      error: { content: error },
    });
    expect(history.messages['response-1']).toBe(responseMessage);
    expect(history.currentId).toBe('response-1');
  });

  it('uses a top-level error message when no provider message is present', () => {
    const { notify } = createHarness(new Error('network timeout'));

    expect(notify).toHaveBeenCalledWith('network timeout');
  });

  it('hides unstructured object errors behind the translated generic message', () => {
    const { notify, translate } = createHarness({
      status: 502,
      body: { secret: 'do not display' },
    });

    expect(notify).toHaveBeenCalledWith(
      'translated:Uh-oh! There was an issue with the response.',
    );
    expect(translate).toHaveBeenCalledWith(
      'Uh-oh! There was an issue with the response.',
    );
  });

  it('preserves plain string errors without translating them', () => {
    const { notify, translate } = createHarness('request failed');

    expect(notify).toHaveBeenCalledWith('request failed');
    expect(translate).not.toHaveBeenCalled();
  });
});

describe('applyOpenAIResponseError', () => {
  const createResponseHarness = (responseMessage: Record<string, any> = { id: 'response-1' }) => {
    const history = { messages: { 'response-1': responseMessage } };
    const getErrorMessage = vi.fn((_error, fallback) => fallback);
    const notify = vi.fn();

    return {
      responseMessage,
      history,
      getErrorMessage,
      notify,
      apply: (error: unknown) =>
        applyOpenAIResponseError({
          error,
          responseMessage,
          history,
          getErrorMessage,
          fallbackMessage: 'Server connection failed',
          notify
        })
    };
  };

  it('completes the response, clears knowledge-search status, and notifies once', () => {
    const { apply, getErrorMessage, history, notify, responseMessage } = createResponseHarness({
      id: 'response-1',
      done: false,
      statusHistory: [
        { action: 'knowledge_search', description: 'Searching' },
        { action: 'tool_call', description: 'Calling tool' }
      ]
    });

    apply({ message: 'provider rejected request' });

    expect(getErrorMessage).toHaveBeenCalledWith(
      { message: 'provider rejected request' },
      'Server connection failed'
    );
    expect(responseMessage).toMatchObject({
      done: true,
      error: { content: 'Server connection failed' },
      statusHistory: [{ action: 'tool_call', description: 'Calling tool' }]
    });
    expect(history.messages['response-1']).toBe(responseMessage);
    expect(notify).toHaveBeenCalledWith('Server connection failed');
  });

  it('does not notify again when the response already contains an error', () => {
    const { apply, notify } = createResponseHarness({
      id: 'response-1',
      done: true,
      error: { content: 'Earlier failure' }
    });

    apply({ message: 'late duplicate failure' });

    expect(notify).not.toHaveBeenCalled();
  });
});
