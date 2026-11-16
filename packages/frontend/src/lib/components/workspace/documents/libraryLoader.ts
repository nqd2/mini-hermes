/** Apply a library snapshot only while its request and selection are current. */
export function createLibraryLoader<
  Document extends { id: string },
  Preview,
>(dependencies: {
  selectedId: () => string | null;
  loadDocuments: () => Promise<Document[]>;
  loadPreviews: (id: string) => Promise<Preview[]>;
  apply: (
    documents: Document[],
    selected: Document | null,
    previews: Preview[],
  ) => void;
  onError: (error: unknown) => void;
  onSettled: () => void;
}) {
  let generation = 0;
  let active = 0;
  let disposed = false;
  return {
    async refresh(background = false) {
      if (disposed || (background && active > 0)) return;
      const request = ++generation;
      const selectedId = dependencies.selectedId();
      const isCurrent = () =>
        !disposed &&
        request === generation &&
        selectedId === dependencies.selectedId();
      active++;
      try {
        const documents = await dependencies.loadDocuments();
        if (!isCurrent()) return;
        const selected =
          documents.find((item) => item.id === selectedId) ?? null;
        const previews = selected
          ? await dependencies.loadPreviews(selected.id)
          : [];
        if (isCurrent()) dependencies.apply(documents, selected, previews);
      } catch (error) {
        if (isCurrent()) dependencies.onError(error);
      } finally {
        active--;
        if (!disposed && request === generation) dependencies.onSettled();
      }
    },
    dispose() {
      disposed = true;
      generation++;
    },
  };
}
