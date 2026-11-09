import { describe, expect, it, vi } from "vitest";
import { createFileSelectionEventController } from "./fileSelectionEventController";

const createHarness = () => {
  let comparisonOpen = false;
  let selectedCount = 2;
  let active = true;
  let directoryMenuOpen = false;
  const container = {} as HTMLElement;
  const modal = {} as Element;
  const clearSelection = vi.fn();
  const closeDirectoryMenu = vi.fn();
  const controller = createFileSelectionEventController({
    isComparisonOpen: () => comparisonOpen,
    getSelectedCount: () => selectedCount,
    clearSelection,
    isActive: () => active,
    isDirectoryMenuOpen: () => directoryMenuOpen,
    closeDirectoryMenu,
    getContainer: () => container,
    isModalPath: (path) => path.includes(modal),
  });

  return {
    controller,
    container,
    modal,
    clearSelection,
    closeDirectoryMenu,
    setComparisonOpen: (value: boolean) => (comparisonOpen = value),
    setSelectedCount: (value: number) => (selectedCount = value),
    setActive: (value: boolean) => (active = value),
    setDirectoryMenuOpen: (value: boolean) => (directoryMenuOpen = value),
  };
};

const clickEvent = (path: EventTarget[]) =>
  ({ composedPath: () => path }) as unknown as MouseEvent;

describe("file selection event controller", () => {
  it("clears a selection on Escape except while comparison is open", () => {
    const harness = createHarness();
    const event = {
      key: "Escape",
      preventDefault: vi.fn(),
    } as unknown as KeyboardEvent;
    harness.setComparisonOpen(true);
    harness.controller.handleKeydown(event);
    expect(event.preventDefault).not.toHaveBeenCalled();

    harness.setComparisonOpen(false);
    harness.setSelectedCount(0);
    harness.controller.handleKeydown(event);
    expect(event.preventDefault).not.toHaveBeenCalled();

    harness.setSelectedCount(2);
    harness.controller.handleKeydown(event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(harness.clearSelection).toHaveBeenCalledOnce();
  });

  it("closes the directory menu and clears selection after an active outside click", () => {
    const harness = createHarness();
    harness.setDirectoryMenuOpen(true);

    harness.controller.handleWindowClick(clickEvent([]));

    expect(harness.closeDirectoryMenu).toHaveBeenCalledOnce();
    expect(harness.clearSelection).toHaveBeenCalledOnce();
  });

  it("preserves selection for clicks inside the file navigator", () => {
    const harness = createHarness();
    harness.setDirectoryMenuOpen(true);

    harness.controller.handleWindowClick(clickEvent([harness.container]));

    expect(harness.closeDirectoryMenu).toHaveBeenCalledOnce();
    expect(harness.clearSelection).not.toHaveBeenCalled();
  });

  it("ignores clicks originating in modal content", () => {
    const harness = createHarness();
    harness.setDirectoryMenuOpen(true);

    harness.controller.handleWindowClick(clickEvent([harness.modal]));

    expect(harness.closeDirectoryMenu).not.toHaveBeenCalled();
    expect(harness.clearSelection).not.toHaveBeenCalled();
  });

  it("ignores window clicks while the navigator is inactive", () => {
    const harness = createHarness();
    harness.setActive(false);
    harness.setDirectoryMenuOpen(true);

    harness.controller.handleWindowClick(clickEvent([]));

    expect(harness.closeDirectoryMenu).not.toHaveBeenCalled();
    expect(harness.clearSelection).not.toHaveBeenCalled();
  });
});
