import { describe, expect, it, vi } from 'vitest';
import {
  findFilesystemChatUploadTerminal,
  uploadFilesystemChatAttachment,
} from './filesystemUpload';

const terminal = {
  id: 'terminal-a',
  url: 'https://terminal.example',
  key: 'secret',
  config: { chat_uploads: 'filesystem' },
  contexts: { chat: { context_id: 'chat_id' } },
};

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  findTerminal: vi.fn(() => terminal),
  getContextDirectory: vi.fn(() => '/workspace'),
  getCwd: vi.fn(async () => ({ cwd: '/from-server' })),
  uploadFile: vi.fn(async () => ({ path: '/workspace/report.pdf', size: 12 })),
  isSavedChat: vi.fn(() => true),
  showFileNavDirectory: vi.fn(),
  touchAttachments: vi.fn(),
  translate: (message: string) => message,
  showError: vi.fn(),
  ...overrides,
});

describe('filesystem chat uploads', () => {
  it('prefers a matching system terminal and falls back to an enabled configured terminal', () => {
    const system = { ...terminal, id: 'server-id' };
    const configured = {
      ...terminal,
      id: undefined,
      url: 'configured-url',
      enabled: true,
    };
    const servers = [system];
    const settings = { terminalServers: [configured] };

    expect(
      findFilesystemChatUploadTerminal('server-id', servers, settings),
    ).toBe(system);
    expect(
      findFilesystemChatUploadTerminal('configured-url', servers, settings),
    ).toBe(configured);
    expect(
      findFilesystemChatUploadTerminal('unknown', servers, settings),
    ).toBeNull();
  });

  it('uploads in the selected chat scope and updates the attachment reference', async () => {
    const fileItem: Record<string, any> = {
      name: 'original.pdf',
      file: new File(['data'], 'original.pdf'),
      terminal_id: 'terminal-a',
      size: 4,
    };
    const sourceFile = fileItem.file;
    const dependencies = makeDependencies();

    await expect(
      uploadFilesystemChatAttachment(fileItem, 'saved-chat', dependencies),
    ).resolves.toBe(true);

    expect(dependencies.getContextDirectory).toHaveBeenCalledWith('terminal-a');
    expect(dependencies.getCwd).not.toHaveBeenCalled();
    expect(dependencies.uploadFile).toHaveBeenCalledWith(
      terminal.url,
      terminal.key,
      '/workspace',
      sourceFile,
      'saved-chat',
    );
    expect(fileItem).toMatchObject({
      status: 'uploaded',
      session_id: 'saved-chat',
      name: 'report.pdf',
      id: '/workspace/report.pdf',
      path: '/workspace/report.pdf',
      url: '/workspace/report.pdf',
      size: 12,
    });
    expect(dependencies.showFileNavDirectory).toHaveBeenCalledWith(
      '/workspace/report.pdf',
    );
    expect(dependencies.touchAttachments).toHaveBeenCalledTimes(2);
  });

  it('resolves the terminal working directory when no current directory is available', async () => {
    const dependencies = makeDependencies({
      getContextDirectory: vi.fn(() => null),
    });

    await expect(
      uploadFilesystemChatAttachment(
        { file: new File(['x'], 'x') },
        'chat-1',
        dependencies,
      ),
    ).resolves.toBe(true);
    expect(dependencies.getCwd).toHaveBeenCalledWith(
      terminal.url,
      terminal.key,
      'chat-1',
    );
    expect(dependencies.uploadFile).toHaveBeenCalledWith(
      terminal.url,
      terminal.key,
      '/from-server',
      expect.any(File),
      'chat-1',
    );
  });

  it.each([
    [
      'missing terminal',
      { findTerminal: vi.fn(() => null) },
      'The attached file’s terminal is unavailable.',
    ],
    ['wrong chat scope', {}, 'Attached files must belong to this chat.'],
    [
      'saved-chat requirement',
      { isSavedChat: vi.fn(() => false) },
      'A saved chat is required for this terminal',
    ],
    [
      'missing working directory',
      {
        getContextDirectory: vi.fn(() => null),
        getCwd: vi.fn(async () => null),
      },
      'Could not determine the upload folder.',
    ],
    [
      'upload failure',
      { uploadFile: vi.fn(async () => null) },
      'Failed to upload file.',
    ],
  ])(
    'blocks %s and exposes an error state',
    async (_label, overrides, expected) => {
      const fileItem: Record<string, any> = {
        name: 'report.pdf',
        file: new File(['x'], 'report.pdf'),
        terminal_id: 'terminal-a',
        session_id:
          expected === 'Attached files must belong to this chat.'
            ? 'other-chat'
            : null,
      };
      const dependencies = makeDependencies(overrides);
      const chatId =
        expected === 'A saved chat is required for this terminal'
          ? null
          : 'chat-1';

      await expect(
        uploadFilesystemChatAttachment(fileItem, chatId, dependencies),
      ).resolves.toBe(false);
      expect(fileItem.status).toBe('error');
      expect(fileItem.error).toBe(`Error: ${expected}`);
      expect(dependencies.showError).toHaveBeenCalledWith(`Error: ${expected}`);
      expect(dependencies.touchAttachments).toHaveBeenCalledTimes(2);
    },
  );
});
