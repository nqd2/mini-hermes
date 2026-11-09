import type { FileUploadEntry } from './fileUploadController';

export const readDroppedFiles = async (
  data: DataTransfer,
): Promise<FileUploadEntry[]> => {
  // Capture all roots before the browser locks the drag data after this event.
  const items = Array.from(data.items ?? []).filter(
    (item) => item.kind === 'file',
  );
  const roots = items.map(
    (item) => item.webkitGetAsEntry?.() ?? item.getAsFile(),
  );
  if (!items.length) roots.push(...Array.from(data.files));

  const entries: FileUploadEntry[] = [];
  async function visit(
    entry: FileSystemEntry | File,
    parent = '',
  ): Promise<void> {
    const path = parent + entry.name;
    if (!('isDirectory' in entry)) {
      entries.push({ path, file: entry });
    } else if (entry.isDirectory) {
      entries.push({ path });
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      while (true) {
        const children = await new Promise<FileSystemEntry[]>(
          (resolve, reject) => reader.readEntries(resolve, reject),
        );
        if (!children.length) break;
        for (const child of children) await visit(child, `${path}/`);
      }
    } else {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      entries.push({ path, file });
    }
  }

  for (const root of roots) {
    if (!root) throw new Error('Unable to read a dropped file or folder.');
    await visit(root);
  }
  return entries;
};
