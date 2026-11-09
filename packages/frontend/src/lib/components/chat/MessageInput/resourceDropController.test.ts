import { describe, expect, it, vi } from 'vitest';
import { createResourceDropController } from './resourceDropController';

const makeEvent = (textData: string, files: File[] = []) => ({
  preventDefault: vi.fn(),
  stopPropagation: vi.fn(),
  dataTransfer: {
    getData: vi.fn(() => textData),
    files,
  },
});

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  getToken: vi.fn(() => 'token'),
  getChat: vi.fn(async () => ({ id: 'chat-1', title: 'Chat title' })),
  getFolder: vi.fn(async () => ({ id: 'folder-1', name: 'Folder name' })),
  getNote: vi.fn(async () => ({ id: 'note-1', title: 'Note title' })),
  findModel: vi.fn(() => ({ id: 'model-1' })),
  addAttachmentIfMissing: vi.fn(),
  selectModel: vi.fn(),
  handleFiles: vi.fn(),
  setDragging: vi.fn(),
  logDrop: vi.fn(),
  logFiles: vi.fn(),
  ...overrides,
});

describe('createResourceDropController', () => {
  it('marks only intentional file and resource drags as active', () => {
    const dependencies = makeDependencies();
    const controller = createResourceDropController(dependencies);
    const event = {
      preventDefault: vi.fn(),
      dataTransfer: { types: ['text/plain', 'application/x-open-webui-drag'] },
    };

    controller.handleDragOver(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(dependencies.setDragging).toHaveBeenCalledWith(true);
  });

  it('does not activate reordering drags and clears dragging when the pointer leaves', () => {
    const dependencies = makeDependencies();
    const controller = createResourceDropController(dependencies);
    const dragOverEvent = {
      preventDefault: vi.fn(),
      dataTransfer: { types: ['text/plain'] },
    };

    controller.handleDragOver(dragOverEvent);
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);

    const currentTarget = { contains: vi.fn(() => false) };
    controller.handleDragLeave({ currentTarget, relatedTarget: {} });
    expect(currentTarget.contains).toHaveBeenCalledWith({});
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
  });

  it('keeps dragging active when the pointer moves within the drop zone', () => {
    const dependencies = makeDependencies();
    const controller = createResourceDropController(dependencies);
    const currentTarget = { contains: vi.fn(() => true) };

    controller.handleDragLeave({ currentTarget, relatedTarget: {} });

    expect(dependencies.setDragging).not.toHaveBeenCalled();
  });

  it.each([
    {
      type: 'chat',
      id: 'chat-1',
      fetchName: 'getChat',
      item: {
        type: 'chat',
        id: 'chat-1',
        name: 'Chat title',
        collection_name: '',
        status: 'processed',
      },
    },
    {
      type: 'folder',
      id: 'folder-1',
      fetchName: 'getFolder',
      item: {
        type: 'folder',
        id: 'folder-1',
        name: 'Folder name',
        status: 'processed',
      },
    },
    {
      type: 'note',
      id: 'note-1',
      fetchName: 'getNote',
      item: {
        type: 'note',
        id: 'note-1',
        name: 'Note title',
        status: 'processed',
      },
    },
  ])(
    'fetches and attaches the dropped $type by its display name',
    async ({ type, id, item, fetchName }) => {
      const dependencies = makeDependencies();
      const controller = createResourceDropController(dependencies);
      const event = makeEvent(JSON.stringify({ type, id }));

      await controller.handleDrop(event);

      expect(dependencies[fetchName]).toHaveBeenCalledWith('token', id);
      expect(dependencies.addAttachmentIfMissing).toHaveBeenCalledWith(
        item,
        expect.any(Function),
      );
      expect(dependencies.handleFiles).not.toHaveBeenCalled();
      expect(event.preventDefault).toHaveBeenCalledOnce();
      expect(event.stopPropagation).toHaveBeenCalledOnce();
      expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
    },
  );

  it('selects a dropped model and consumes its payload even when no matching model exists', async () => {
    const dependencies = makeDependencies({
      findModel: vi.fn(() => undefined),
    });
    const controller = createResourceDropController(dependencies);
    const event = makeEvent(
      JSON.stringify({ type: 'model', id: 'missing-model' }),
    );

    await controller.handleDrop(event);

    expect(dependencies.findModel).toHaveBeenCalledWith('missing-model');
    expect(dependencies.selectModel).not.toHaveBeenCalled();
    expect(event.stopPropagation).toHaveBeenCalledOnce();
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
  });

  it('falls back to file handling for invalid or unrecognized payloads', async () => {
    const droppedFile = new File(['data'], 'report.txt', {
      type: 'text/plain',
    });
    const dependencies = makeDependencies();
    const controller = createResourceDropController(dependencies);
    const event = makeEvent('not-json', [droppedFile]);

    await controller.handleDrop(event);

    expect(dependencies.handleFiles).toHaveBeenCalledWith([droppedFile]);
    expect(event.stopPropagation).not.toHaveBeenCalled();
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
  });

  it('consumes recognized resources even when the lookup returns no item', async () => {
    const dependencies = makeDependencies({ getNote: vi.fn(async () => null) });
    const controller = createResourceDropController(dependencies);
    const event = makeEvent(
      JSON.stringify({ type: 'note', id: 'missing-note' }),
    );

    await controller.handleDrop(event);

    expect(dependencies.addAttachmentIfMissing).not.toHaveBeenCalled();
    expect(dependencies.handleFiles).not.toHaveBeenCalled();
    expect(event.stopPropagation).toHaveBeenCalledOnce();
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
  });

  it('falls through to file handling when a recognized-resource lookup rejects', async () => {
    const droppedFile = new File(['data'], 'fallback.txt', {
      type: 'text/plain',
    });
    const dependencies = makeDependencies({
      getChat: vi.fn(async () => {
        throw new Error('lookup failed');
      }),
    });
    const controller = createResourceDropController(dependencies);
    const event = makeEvent(JSON.stringify({ type: 'chat', id: 'chat-1' }), [
      droppedFile,
    ]);

    await controller.handleDrop(event);

    expect(dependencies.handleFiles).toHaveBeenCalledWith([droppedFile]);
    expect(event.stopPropagation).not.toHaveBeenCalled();
    expect(dependencies.setDragging).toHaveBeenLastCalledWith(false);
  });
});
