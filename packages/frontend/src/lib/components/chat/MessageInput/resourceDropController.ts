type ResourceDropEvent = {
  preventDefault: () => void;
  stopPropagation: () => void;
  dataTransfer?: {
    getData: (format: string) => string;
    files?: ArrayLike<File> | null;
  } | null;
};

type ResourceDropItem = Record<string, unknown>;

export type ResourceDropControllerDependencies<ModelValue> = {
  getToken: () => string;
  getChat: (
    token: string,
    id: string,
  ) => Promise<{ id: string; title?: string | null } | null | undefined>;
  getFolder: (
    token: string,
    id: string,
  ) => Promise<{ id: string; name?: string | null } | null | undefined>;
  getNote: (
    token: string,
    id: string,
  ) => Promise<{ id: string; title?: string | null } | null | undefined>;
  findModel: (id: string) => ModelValue | null | undefined;
  addAttachmentIfMissing: (
    item: ResourceDropItem,
    isDuplicate: (candidate: { id?: string }) => boolean,
  ) => void;
  selectModel: (model: ModelValue) => void;
  handleFiles: (files: File[]) => void;
  setDragging: (dragging: boolean) => void;
  logDrop: (event: ResourceDropEvent) => void;
  logFiles: (files: File[]) => void;
};

export const createResourceDropController = <ModelValue>(
  dependencies: ResourceDropControllerDependencies<ModelValue>,
) => {
  const finishResourceDrop = (event: ResourceDropEvent) => {
    dependencies.setDragging(false);
    event.stopPropagation();
  };

  const handleDrop = async (event: ResourceDropEvent): Promise<void> => {
    event.preventDefault();
    dependencies.logDrop(event);

    const textData = event.dataTransfer?.getData('text/plain');
    if (textData) {
      try {
        const data = JSON.parse(textData);
        if (data.type === 'chat' && data.id) {
          const chat = await dependencies.getChat(
            dependencies.getToken(),
            data.id,
          );
          if (chat) {
            const item = {
              type: 'chat',
              id: chat.id,
              name: chat.title,
              collection_name: '',
              status: 'processed',
            };
            dependencies.addAttachmentIfMissing(
              item,
              (candidate) => candidate.id === item.id,
            );
          }
          finishResourceDrop(event);
          return;
        } else if (data.type === 'folder' && data.id) {
          const folder = await dependencies.getFolder(
            dependencies.getToken(),
            data.id,
          );
          if (folder) {
            const item = {
              type: 'folder',
              id: folder.id,
              name: folder.name,
              status: 'processed',
            };
            dependencies.addAttachmentIfMissing(
              item,
              (candidate) => candidate.id === item.id,
            );
          }
          finishResourceDrop(event);
          return;
        } else if (data.type === 'note' && data.id) {
          const note = await dependencies.getNote(
            dependencies.getToken(),
            data.id,
          );
          if (note) {
            const item = {
              type: 'note',
              id: note.id,
              name: note.title,
              status: 'processed',
            };
            dependencies.addAttachmentIfMissing(
              item,
              (candidate) => candidate.id === item.id,
            );
          }
          finishResourceDrop(event);
          return;
        } else if (data.type === 'model' && data.id) {
          const model = dependencies.findModel(data.id);
          if (model) {
            dependencies.selectModel(model);
          }
          finishResourceDrop(event);
          return;
        }
      } catch {
        // Malformed payloads and failed lookups fall through to file handling.
      }
    }

    if (event.dataTransfer?.files) {
      const files = Array.from(event.dataTransfer.files);
      if (files.length > 0) {
        dependencies.logFiles(files);
        dependencies.handleFiles(files);
      }
    }

    dependencies.setDragging(false);
  };

  return { handleDrop };
};
