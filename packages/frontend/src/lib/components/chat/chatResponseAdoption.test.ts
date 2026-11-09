import { describe, expect, it, vi } from 'vitest';
import { adoptChatResponse } from './chatResponseAdoption';

const createHarness = (overrides: Record<string, any> = {}) => {
  const queue = {
    'request-chat': [
      {
        id: 'queued-1',
        files: [
          { type: 'filesystem', path: '/tmp/a.txt', session_id: null },
          { type: 'document', id: 'doc-1' },
        ],
      },
    ],
    'target-chat': [{ id: 'already-queued', files: [] }],
  };
  const userMessage = {
    files: [{ type: 'filesystem', session_id: null }, { type: 'upload', id: 'upload-1' }],
  };
  const parentMessage = { files: [{ type: 'filesystem', session_id: null }] };
  const queueStore = {
    update: vi.fn((update) => (queueStore.value = update(queueStore.value))),
    value: queue,
  };
  const actions = {
    setChatId: vi.fn(),
    getDraft: vi.fn(() => 'draft text'),
    saveDraft: vi.fn(),
    replaceUrl: vi.fn(),
    refreshChatList: vi.fn(),
    updateChatParams: vi.fn(),
    processNextInQueue: vi.fn(),
  };

  return {
    queueStore,
    userMessage,
    parentMessage,
    actions,
    input: {
      responseChatId: 'target-chat',
      requestChatId: 'request-chat',
      activeChatId: 'request-chat',
      hasResponseMessage: true,
      userMessage,
      parentMessage,
      queueStore,
      temporaryChatEnabled: false,
      embedded: false,
      token: 'token-1',
      params: { temperature: 0.4 },
      ...actions,
      ...overrides,
    },
  };
};

describe('adoptChatResponse', () => {
  it('migrates queued filesystem scope and completes saved-chat setup', async () => {
    const { actions, input, parentMessage, queueStore, userMessage } = createHarness();

    await expect(adoptChatResponse(input)).resolves.toBe(true);

    expect(queueStore.value['request-chat']).toBeUndefined();
    expect(queueStore.value['target-chat'].map(({ id }) => id)).toEqual([
      'queued-1',
      'already-queued',
    ]);
    expect(queueStore.value['target-chat'][0].files[0].session_id).toBe('target-chat');
    expect(queueStore.value['target-chat'][0].files[1]).toEqual({
      type: 'document',
      id: 'doc-1',
    });
    expect(userMessage.files[0].session_id).toBe('target-chat');
    expect(parentMessage.files[0].session_id).toBe('target-chat');
    expect(actions.setChatId).toHaveBeenCalledWith('target-chat');
    expect(actions.saveDraft).toHaveBeenCalledWith('draft text', 'target-chat', false);
    expect(actions.replaceUrl).toHaveBeenCalledWith('/c/target-chat');
    expect(actions.refreshChatList).toHaveBeenCalledWith('token-1');
    expect(actions.updateChatParams).toHaveBeenCalledWith('token-1', 'target-chat', {
      temperature: 0.4,
    });
    expect(actions.processNextInQueue).toHaveBeenCalledWith('target-chat');
  });

  it('does nothing when the request is stale or navigation moved elsewhere', async () => {
    const { actions, input, queueStore } = createHarness({ activeChatId: 'other-chat' });

    await expect(adoptChatResponse(input)).resolves.toBe(false);
    expect(queueStore.update).not.toHaveBeenCalled();
    expect(actions.setChatId).not.toHaveBeenCalled();
    expect(actions.processNextInQueue).not.toHaveBeenCalled();
  });

  it('skips saved-chat persistence for temporary and embedded chats', async () => {
    const { actions, input } = createHarness({ temporaryChatEnabled: true, embedded: true });

    await expect(adoptChatResponse(input)).resolves.toBe(true);
    expect(actions.setChatId).toHaveBeenCalledWith('target-chat');
    expect(actions.saveDraft).not.toHaveBeenCalled();
    expect(actions.replaceUrl).not.toHaveBeenCalled();
    expect(actions.refreshChatList).not.toHaveBeenCalled();
    expect(actions.updateChatParams).not.toHaveBeenCalled();
    expect(actions.processNextInQueue).toHaveBeenCalledWith('target-chat');
  });
});
