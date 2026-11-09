import { describe, expect, it } from 'vitest';
import { buildChatRequestPayload } from './chatRequestPayload';

const toolRouting = {
  filterIds: ['filter-1'],
  toolIds: ['tool-1'],
  skillIds: ['skill-1'],
  terminalId: 'terminal-1',
  toolServers: [{ id: 'server-1' }],
};

const createInput = (overrides: Record<string, any> = {}) => ({
  stream: true,
  modelId: 'model-1',
  messages: [{ role: 'user', content: 'hello' }],
  settingsParams: { temperature: 0.5, top_p: 0.9, model_controls: { seed: 7 } },
  modelParams: { temperature: 0.2, ignored: null },
  stop: ['END'],
  files: [{ id: 'file-1' }],
  embedded: false,
  noteFiles: [{ id: 'note-1' }],
  toolRouting,
  features: { web_search: true },
  variables: { name: 'Duc' },
  includeChatVariables: false,
  chatVariables: { project: 'alpha' },
  modelItem: { id: 'model-1' },
  sessionId: 'socket-1',
  chatId: 'chat-1',
  folderId: 'folder-1',
  responseMessageId: 'response-1',
  messageIdsList: undefined,
  userMessage: { id: 'user-1', parentId: 'parent-1' },
  regenerationPrompt: undefined,
  regenerationMessageId: undefined,
  continueResponse: false,
  backgroundTasks: { follow_up_generation: true },
  ...overrides,
});

describe('buildChatRequestPayload', () => {
  it('preserves provider payload fields while filtering null model params', () => {
    const input = createInput({ messages: [] });

    expect(buildChatRequestPayload(input)).toEqual({
      stream: true,
      model: 'model-1',
      params: {
        temperature: 0.2,
        top_p: 0.9,
        model_controls: { seed: 7 },
        stop: ['END'],
      },
      files: [{ id: 'file-1' }],
      filter_ids: ['filter-1'],
      tool_ids: ['tool-1'],
      skill_ids: ['skill-1'],
      terminal_id: 'terminal-1',
      tool_servers: [{ id: 'server-1' }],
      features: { web_search: true },
      variables: { name: 'Duc' },
      model_item: { id: 'model-1' },
      session_id: 'socket-1',
      chat_id: 'chat-1',
      folder_id: 'folder-1',
      id: 'response-1',
      parent_id: 'parent-1',
      user_message: { id: 'user-1', parentId: 'parent-1' },
      background_tasks: { follow_up_generation: true },
    });
  });

  it('includes system and note attachments only for embedded chats', () => {
    const payload = buildChatRequestPayload(createInput({ embedded: true }));

    expect(payload.files).toEqual([{ id: 'file-1' }, { id: 'note-1' }]);
    expect(payload.messages).toEqual([{ role: 'user', content: 'hello' }]);
  });

  it('omits empty file lists and includes request continuation metadata when present', () => {
    const payload = buildChatRequestPayload(
      createInput({
        files: [],
        includeChatVariables: true,
        messageIdsList: [{ model_id: 'model-1', message_id: 'response-1' }],
        regenerationPrompt: 'make it shorter',
        regenerationMessageId: 'previous-response',
        continueResponse: true,
      }),
    );

    expect(payload.files).toBeUndefined();
    expect(payload.chat_variables).toEqual({ project: 'alpha' });
    expect(payload.message_ids).toEqual([
      { model_id: 'model-1', message_id: 'response-1' },
    ]);
    expect(payload.regeneration_prompt).toBe('make it shorter');
    expect(payload.regeneration_message_id).toBe('previous-response');
    expect(payload.assistant_message_id).toBe('response-1');
  });
});
