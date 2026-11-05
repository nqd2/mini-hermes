import { derived, get, writable } from "svelte/store";
import { showSidebar } from './index';

const openViewers = writable<Set<symbol>>(new Set());
let sidebarBeforeViewer = false;
let viewerWasOpen = false;

openViewers.subscribe((viewers) => {
  const isOpen = viewers.size > 0;
  if (isOpen && !viewerWasOpen) {
    sidebarBeforeViewer = get(showSidebar);
    showSidebar.set(false);
  } else if (!isOpen && viewerWasOpen) {
    showSidebar.set(sidebarBeforeViewer);
  }
  viewerWasOpen = isOpen;
});

/** Used by chat layout to reserve room for an open right-hand document viewer. */
export const documentViewerOpen = derived(
  openViewers,
  ($openViewers) => $openViewers.size > 0,
);

export const setDocumentViewerDock = (viewer: symbol, open: boolean) => {
  openViewers.update((current) => {
    const next = new Set(current);
    if (open) next.add(viewer);
    else next.delete(viewer);
    return next;
  });
};

/** Portals a dock to the page so transformed chat ancestors cannot constrain fixed positioning. */
export const portalDocumentViewer = (node: HTMLElement) => {
  document.body.appendChild(node);
  return { destroy: () => node.remove() };
};
