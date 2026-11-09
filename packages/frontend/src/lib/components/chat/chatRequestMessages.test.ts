import { describe, expect, it, vi } from 'vitest';
import { buildChatRequestMessages } from './chatRequestMessages';

const isRasterImageContentType = (contentType?: string) =>
  contentType?.startsWith('image/') ?? false;

describe('buildChatRequestMessages', () => {
  it('sends the system prompt without local history for persisted chats', () => {
    const processDetails = vi.fn((content: string) => `processed:${content}`);

    const messages = buildChatRequestMessages({
      systemPrompt: 'system instructions',
      isTemporaryChat: false,
      historyMessages: [{ role: 'user', content: 'private persisted history' }],
      processDetails,
      isRasterImageContentType,
    });

    expect(messages).toEqual([
      { role: 'system', content: 'system instructions' },
    ]);
    expect(processDetails).not.toHaveBeenCalled();
  });

  it('builds temporary history with processed text and image attachments', () => {
    const processDetails = vi.fn((content: string) => `processed:${content}`);
    const messages = buildChatRequestMessages({
      systemPrompt: '',
      isTemporaryChat: true,
      historyMessages: [
        {
          id: 'user-1',
          role: 'user',
          content: 'original',
          files: [
            { type: 'image', url: 'data:image/png;base64,inline' },
            { type: 'file', content_type: 'image/jpeg', url: 'https://image' },
            {
              type: 'file',
              content_type: 'application/pdf',
              url: 'https://pdf',
            },
          ],
        },
      ],
      processDetails,
      isRasterImageContentType,
    });

    expect(messages).toEqual([
      {
        id: 'user-1',
        role: 'user',
        contextSummary: undefined,
        content: [
          { type: 'text', text: 'processed:original' },
          {
            type: 'image_url',
            image_url: { url: 'data:image/png;base64,inline' },
          },
          { type: 'image_url', image_url: { url: 'https://image' } },
        ],
      },
    ]);
    expect(processDetails).toHaveBeenCalledWith('original');
  });

  it('preserves assistant outputs and removes blank assistant messages', () => {
    const messages = buildChatRequestMessages({
      systemPrompt: undefined,
      isTemporaryChat: true,
      historyMessages: [
        { id: 'assistant-empty', role: 'assistant', content: '   ' },
        {
          id: 'assistant-output',
          role: 'assistant',
          output: [{ type: 'text', text: 'done' }],
          model: 'model-a',
          meta: { source: 'tool' },
        },
      ],
      processDetails: (content) => content,
      isRasterImageContentType,
    });

    expect(messages).toEqual([
      {
        role: 'assistant',
        model: 'model-a',
        output: [{ type: 'text', text: 'done' }],
        meta: { source: 'tool' },
      },
    ]);
  });

  it('uses merged user content while retaining ordinary message metadata', () => {
    const messages = buildChatRequestMessages({
      systemPrompt: null,
      isTemporaryChat: true,
      historyMessages: [
        {
          id: 'user-2',
          role: 'user',
          content: 'raw content',
          merged: { content: 'merged content' },
          contextSummary: 'previous turn summary',
          model: 'model-b',
          meta: { tag: 'kept' },
        },
      ],
      processDetails: (content) => content,
      isRasterImageContentType,
    });

    expect(messages).toEqual([
      {
        id: 'user-2',
        role: 'user',
        contextSummary: 'previous turn summary',
        content: 'merged content',
        model: 'model-b',
        meta: { tag: 'kept' },
      },
    ]);
  });
});
