import { describe, expect, it, vi } from 'vitest';
import { readDroppedFiles } from './droppedFileReader';

const makeDataTransfer = (items: unknown[], files: File[] = []) =>
  ({ items, files }) as unknown as DataTransfer;

const makeFileEntry = (name: string, file: File) => ({
  name,
  isFile: true,
  isDirectory: false,
  file: (resolve: (file: File) => void) => resolve(file),
});

const makeDirectoryEntry = (name: string, batches: unknown[][]) => ({
  name,
  isFile: false,
  isDirectory: true,
  createReader: () => ({
    readEntries: (resolve: (entries: unknown[]) => void) =>
      resolve(batches.shift() ?? []),
  }),
});

const makeFileItem = (entry: unknown, file: File | null = null) => ({
  kind: 'file',
  webkitGetAsEntry: vi.fn(() => entry),
  getAsFile: vi.fn(() => file),
});

describe('readDroppedFiles', () => {
  it('recursively reads every batch of entries and preserves directory paths', async () => {
    const firstFile = new File(['a'], 'first.txt');
    const secondFile = new File(['b'], 'second.txt');
    const nestedDirectory = makeDirectoryEntry('nested', [
      [makeFileEntry('second.txt', secondFile)],
      [],
    ]);
    const rootDirectory = makeDirectoryEntry('root', [
      [makeFileEntry('first.txt', firstFile), nestedDirectory],
      [],
    ]);

    const result = await readDroppedFiles(
      makeDataTransfer([makeFileItem(rootDirectory)]),
    );

    expect(result).toEqual([
      { path: 'root' },
      { path: 'root/first.txt', file: firstFile },
      { path: 'root/nested' },
      { path: 'root/nested/second.txt', file: secondFile },
    ]);
  });

  it('falls back to FileList when no file items are available', async () => {
    const file = new File(['text'], 'standalone.txt');

    await expect(
      readDroppedFiles(makeDataTransfer([], [file])),
    ).resolves.toEqual([{ path: 'standalone.txt', file }]);
  });

  it('uses getAsFile when an item has no filesystem entry', async () => {
    const file = new File(['text'], 'entry-fallback.txt');
    const item = makeFileItem(null, file);

    await expect(readDroppedFiles(makeDataTransfer([item]))).resolves.toEqual([
      { path: 'entry-fallback.txt', file },
    ]);
    expect(item.getAsFile).toHaveBeenCalledOnce();
  });

  it('fails clearly when a file item cannot be read by either browser API', async () => {
    const item = makeFileItem(null, null);

    await expect(readDroppedFiles(makeDataTransfer([item]))).rejects.toThrow(
      'Unable to read a dropped file or folder.',
    );
  });

  it('propagates directory reader failures', async () => {
    const error = new Error('directory read failed');
    const directory = {
      name: 'root',
      isFile: false,
      isDirectory: true,
      createReader: () => ({
        readEntries: (
          _resolve: (entries: unknown[]) => void,
          reject: (error: Error) => void,
        ) => reject(error),
      }),
    };

    await expect(
      readDroppedFiles(makeDataTransfer([makeFileItem(directory)])),
    ).rejects.toBe(error);
  });

  it('propagates file-entry callback failures', async () => {
    const error = new Error('file read failed');
    const fileEntry = {
      name: 'broken.txt',
      isFile: true,
      isDirectory: false,
      file: (_resolve: (file: File) => void, reject: (error: Error) => void) =>
        reject(error),
    };

    await expect(
      readDroppedFiles(makeDataTransfer([makeFileItem(fileEntry)])),
    ).rejects.toBe(error);
  });
});
