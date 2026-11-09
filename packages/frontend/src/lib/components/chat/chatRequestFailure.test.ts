import { describe, expect, it, vi } from 'vitest';
import { applyChatRequestFailure } from './chatRequestFailure';

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
