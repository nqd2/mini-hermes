import { describe, expect, it, vi } from 'vitest';
import { loadCurrentFilePreviewRequest } from './filePreviewRequest';
import { createEmptyFilePreviewData } from './filePreviewLoader';

const terminal = { url: 'https://terminal.example', key: 'secret' };

describe('loadCurrentFilePreviewRequest', () => {
  it('releases object URLs when a preview result belongs to a stale selection', async () => {
    const preview = {
      ...createEmptyFilePreviewData(),
      fileImageUrl: 'blob:image',
      fileVideoUrl: 'blob:video',
      fileAudioUrl: 'blob:audio',
    };
    const loadPreview = vi.fn(async () => preview);
    const revokeObjectURL = vi.fn();

    await expect(
      loadCurrentFilePreviewRequest({
        path: '/workspace/old.png',
        terminal,
        sessionId: 'chat-1',
        translate: (key) => key,
        isCurrent: () => false,
        loadPreview,
        revokeObjectURL,
      }),
    ).resolves.toEqual({ status: 'stale' });

    expect(loadPreview).toHaveBeenCalledWith(
      '/workspace/old.png',
      terminal,
      'chat-1',
      expect.any(Function),
    );
    expect(revokeObjectURL.mock.calls).toEqual([
      ['blob:image'],
      ['blob:video'],
      ['blob:audio'],
    ]);
  });

  it('returns current preview data without revoking its URLs', async () => {
    const preview = {
      ...createEmptyFilePreviewData(),
      fileImageUrl: 'blob:current',
    };
    const revokeObjectURL = vi.fn();

    await expect(
      loadCurrentFilePreviewRequest({
        path: '/workspace/current.png',
        terminal,
        sessionId: null,
        translate: (key) => key,
        isCurrent: () => true,
        loadPreview: vi.fn(async () => preview),
        revokeObjectURL,
      }),
    ).resolves.toEqual({ status: 'loaded', preview });

    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it('propagates preview loader failures to the caller unchanged', async () => {
    const error = new Error('preview reader failed');
    const revokeObjectURL = vi.fn();

    await expect(
      loadCurrentFilePreviewRequest({
        path: '/workspace/report.txt',
        terminal,
        sessionId: null,
        translate: (key) => key,
        isCurrent: () => true,
        loadPreview: vi.fn(async () => {
          throw error;
        }),
        revokeObjectURL,
      }),
    ).rejects.toBe(error);
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });
});
