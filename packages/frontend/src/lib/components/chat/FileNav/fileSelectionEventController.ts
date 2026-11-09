export type FileSelectionEventControllerDependencies = {
  isComparisonOpen: () => boolean;
  getSelectedCount: () => number;
  clearSelection: () => void;
  isActive: () => boolean;
  isDirectoryMenuOpen: () => boolean;
  closeDirectoryMenu: () => void;
  getContainer: () => EventTarget | null;
  isModalPath: (path: EventTarget[]) => boolean;
};

/** Keeps global selection shortcuts and outside-click behavior out of FileNav rendering. */
export const createFileSelectionEventController = (
  dependencies: FileSelectionEventControllerDependencies,
) => {
  const handleKeydown = (event: KeyboardEvent) => {
    if (dependencies.isComparisonOpen()) return;
    if (event.key === "Escape" && dependencies.getSelectedCount() > 0) {
      event.preventDefault();
      dependencies.clearSelection();
    }
  };

  const handleWindowClick = (event: MouseEvent) => {
    if (!dependencies.isActive()) return;

    const path = event.composedPath();
    // A close click can reach window after its modal has already been destroyed.
    if (dependencies.isModalPath(path)) return;

    if (dependencies.isDirectoryMenuOpen()) {
      dependencies.closeDirectoryMenu();
    }

    const container = dependencies.getContainer();
    if (
      !dependencies.isComparisonOpen() &&
      dependencies.getSelectedCount() > 0 &&
      container &&
      !path.includes(container)
    ) {
      dependencies.clearSelection();
    }
  };

  return { handleKeydown, handleWindowClick };
};
