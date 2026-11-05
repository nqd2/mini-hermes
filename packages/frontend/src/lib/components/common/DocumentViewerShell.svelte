<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { WEBUI_API_BASE_URL } from '$lib/constants';
  import { portalDocumentViewer, setDocumentViewerDock } from '$lib/stores/documentViewerDock';
  import ArrowLeft from '$lib/components/icons/ArrowLeft.svelte';
  import Check from '$lib/components/icons/Check.svelte';
  import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
  import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
  import Collapse from '$lib/components/icons/Collapse.svelte';
  import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
  import Download from '$lib/components/icons/Download.svelte';
  import Expand from '$lib/components/icons/Expand.svelte';
  import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
  import XMark from '$lib/components/icons/XMark.svelte';

  export let filename = 'File';
  export let documentId: string | null = null;
  export let revision: number | null = null;
  export let size: number | null = null;
  export let location = 'Personal Folder';
  export let accessLabel = 'Can view';
  export let onClose: () => void;
  export let onDownload: () => void;
  export let onRevisionSelect: (revision: number) => void = () => {};
  export let onShowText: (() => void) | null = null;
  export let textMode = false;
  export let onShowOriginal: (() => void) | null = null;
  export let originalMode = false;
  export let pageCountOverride = 0;
  export let currentPageOverride = 0;
  export let onPageSelect: (page: number) => void = () => {};
  export let zoom: number | 'fit' = 'fit';
  export let otherFiles: Array<{ id: string; filename: string }> = [];
  export let onFileSelect: (id: string) => void = () => {};

  type RevisionItem = { number: number; created_at: string; size: number; current: boolean };
  const dockToken = Symbol('document-viewer');
  let fullView = false;
  let menuOpen = false;
  let zoomMenuOpen = false;
  let detailPanel: 'info' | 'history' | null = null;
  let revisions: RevisionItem[] = [];
  let loadedDocument = '';
  let viewport: HTMLDivElement;
  let content: HTMLDivElement;
  let pageCount = 0;
  let currentPage = 1;
  let observer: MutationObserver | null = null;

  $: if (documentId && documentId !== loadedDocument) {
    loadedDocument = documentId;
    void loadRevisions(documentId);
  }
  $: if (!documentId) {
    loadedDocument = '';
    revisions = [];
  }
  $: effectivePageCount = pageCountOverride || pageCount;
  $: effectiveCurrentPage = currentPageOverride || currentPage;

  const loadRevisions = async (id: string) => {
    try {
      const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes/documents/${encodeURIComponent(id)}/revisions`, {
        headers: { Authorization: `Bearer ${localStorage.token}` }
      });
      if (response.ok && loadedDocument === id) revisions = await response.json();
    } catch {
      revisions = [];
    }
  };

  const pages = () => Array.from(content?.querySelectorAll<HTMLElement>('section.docx, .pdf-page-wrapper') ?? []);
  const updatePages = () => {
    const elements = pages();
    pageCount = elements.length;
    if (!elements.length) return;
    const top = viewport.getBoundingClientRect().top + 24;
    let nearest = 0;
    let distance = Infinity;
    elements.forEach((element, index) => {
      const delta = Math.abs(element.getBoundingClientRect().top - top);
      if (delta < distance) { nearest = index; distance = delta; }
    });
    currentPage = nearest + 1;
  };
  const navigate = (page: number) => {
    if (pageCountOverride) {
      onPageSelect(page);
      return;
    }
    pages()[page - 1]?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    currentPage = page;
  };
  const closeMenus = () => { menuOpen = false; zoomMenuOpen = false; };
  const handleKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      if (detailPanel) detailPanel = null;
      else if (menuOpen || zoomMenuOpen) closeMenus();
      else if (fullView) fullView = false;
      else onClose();
    }
  };
  onMount(async () => {
    setDocumentViewerDock(dockToken, true);
    await tick();
    observer = new MutationObserver(updatePages);
    observer.observe(content, { childList: true, subtree: true });
    updatePages();
    window.addEventListener('keydown', handleKey);
  });
  onDestroy(() => {
    observer?.disconnect();
    window.removeEventListener('keydown', handleKey);
    setDocumentViewerDock(dockToken, false);
  });
</script>

<aside
  use:portalDocumentViewer
  aria-label={filename}
  class="fixed inset-0 z-[100] flex flex-col overflow-hidden border-l border-white/10 bg-[#101010] text-white md:left-auto {fullView ? 'md:w-full' : 'md:w-1/2'}"
>
  <header class="relative z-20 flex h-[4.5rem] shrink-0 items-center justify-between gap-2 px-3 md:px-4">
    <div class="relative min-w-0">
      <button type="button" class="flex max-w-[min(42vw,23rem)] items-center gap-2 rounded-full bg-[#303030] px-3.5 py-2 text-sm hover:bg-[#3a3a3a]" aria-expanded={menuOpen} on:click={() => { menuOpen = !menuOpen; zoomMenuOpen = false; }}>
        <DocumentPage className="size-4 shrink-0 text-gray-200" /><span class="truncate">{filename}</span><ChevronDown className="size-3.5 shrink-0 text-gray-300" strokeWidth="2" />
      </button>
      {#if menuOpen}
        <div class="absolute left-0 top-full mt-2 w-64 rounded-2xl border border-white/10 bg-[#303030] p-2 shadow-2xl">
          <div class="flex items-start gap-2.5 border-b border-white/10 px-3 py-2">
            <DocumentPage className="mt-0.5 size-4 shrink-0 text-gray-300" />
            <div class="min-w-0">
              <div class="break-words text-xs font-medium text-gray-100">{filename}</div>
              <div class="text-xs text-gray-400">{location}</div>
            </div>
          </div>
          <button class="mt-1 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10" on:click={() => { detailPanel = 'info'; closeMenus(); }}><InfoCircle className="size-4 shrink-0" /><span>File info</span></button>
          {#if onShowOriginal}<button class="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10" on:click={() => { onShowOriginal?.(); closeMenus(); }}>{originalMode ? 'Proposed revision' : 'Original revision'}</button>{/if}
          {#if onShowText}<button class="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10" on:click={() => { onShowText?.(); closeMenus(); }}>{textMode ? 'Rendered preview' : 'Extracted text'}</button>{/if}
          {#if documentId && revisions.length}
            <button class="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10" on:click={() => { detailPanel = 'history'; closeMenus(); }}><ClockRotateRight className="size-4 shrink-0" /><span>Version history</span></button>
          {/if}
          {#if otherFiles.length > 1}
            <div class="mt-2 border-t border-white/10 px-3 pt-3 text-xs text-gray-400">Files in chat</div>
            {#each otherFiles as file (file.id)}
              <button class="block w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10" title={file.filename} on:click={() => { onFileSelect(file.id); closeMenus(); }}>{file.filename}</button>
            {/each}
          {/if}
        </div>
      {/if}
    </div>
    <div class="flex shrink-0 items-center gap-1.5">
      <div class="relative">
        <button class="flex items-center gap-1.5 rounded-full bg-[#303030] px-3.5 py-2 text-sm hover:bg-[#3a3a3a]" aria-label="Zoom" aria-expanded={zoomMenuOpen} on:click={() => { zoomMenuOpen = !zoomMenuOpen; menuOpen = false; }}><span>{zoom === 'fit' ? 'Fit' : `${zoom}%`}</span><ChevronDown className="size-3.5 shrink-0 text-gray-300" strokeWidth="2" /></button>
        {#if zoomMenuOpen}
          <div class="absolute right-0 top-full mt-2 w-44 rounded-2xl border border-white/10 bg-[#303030] p-2 shadow-2xl">
            {#each [25, 50, 100, 200, 'fit'] as value}
              <button class="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10 {value === 'fit' ? 'mt-1 border-t border-white/10 pt-2.5' : ''}" on:click={() => { zoom = value as number | 'fit'; zoomMenuOpen = false; }}><span>{value === 'fit' ? 'Zoom to fit' : `${value}%`}</span>{#if zoom === value}<Check className="size-4 shrink-0" strokeWidth="2" />{/if}</button>
            {/each}
          </div>
        {/if}
      </div>
      <button class="hidden items-center justify-center rounded-full bg-[#303030] p-2.5 text-sm hover:bg-[#3a3a3a] sm:inline-flex" aria-label={fullView ? 'Exit full view' : 'Enter full view'} on:click={() => fullView = !fullView}>{#if fullView}<Collapse className="size-4" />{:else}<Expand className="size-4" />{/if}</button>
      <button class="inline-flex items-center justify-center rounded-full bg-[#303030] p-2.5 text-sm hover:bg-[#3a3a3a]" aria-label="Back to chat" on:click={onClose}><ArrowLeft className="size-4 md:hidden" /><XMark className="hidden size-4 md:block" /></button>
      <button class="inline-flex items-center justify-center rounded-full bg-[#303030] p-2.5 text-sm hover:bg-[#3a3a3a]" aria-label="Download file" on:click={onDownload}><Download className="size-4" strokeWidth="1.75" /></button>
    </div>
  </header>
  <div class="relative min-h-0 flex-1">
    <div bind:this={viewport} class="h-full overflow-auto overscroll-contain" on:scroll|capture={updatePages}>
      <div bind:this={content} class="h-full min-h-0 px-4 pt-2 md:px-8">
        <slot />
      </div>
    </div>
    {#if effectivePageCount > 1}
      <nav class="absolute right-1 top-1/2 z-10 flex max-h-44 -translate-y-1/2 flex-col gap-1 overflow-auto rounded-full bg-[#252525]/80 px-1.5 py-2" aria-label="Document pages">
        {#each Array(effectivePageCount) as _, index}
          <button type="button" class="h-1.5 w-4 rounded-full {effectiveCurrentPage === index + 1 ? 'bg-white' : 'bg-white/30 hover:bg-white/70'}" aria-label={`Go to page ${index + 1}`} title={`Page ${index + 1} of ${effectivePageCount}`} on:click={() => navigate(index + 1)}></button>
        {/each}
      </nav>
    {/if}
    {#if detailPanel}
      <section class="absolute inset-y-0 right-0 z-20 w-[min(22rem,85vw)] overflow-auto rounded-l-2xl bg-[#303030] p-5 shadow-2xl" aria-label={detailPanel === 'info' ? 'File info' : 'Version history'}>
        <div class="mb-6 flex items-center justify-between"><h2 class="text-base font-semibold">{detailPanel === 'info' ? 'File info' : 'Version history'}</h2><button class="inline-flex items-center justify-center rounded-full p-1.5 hover:bg-white/10" aria-label="Close details" on:click={() => detailPanel = null}><XMark className="size-4" /></button></div>
        {#if detailPanel === 'info'}
          <p class="mb-2 break-words text-sm">{filename}</p>
          <p class="mb-6 text-sm text-gray-400">{location}</p>
          <dl class="grid grid-cols-[5rem_1fr] gap-y-4 border-t border-white/10 pt-5 text-sm">
            <dt class="text-gray-400">Type</dt><dd>{filename.split('.').pop()?.toUpperCase() || 'File'}</dd>
            <dt class="text-gray-400">Size</dt><dd>{size == null ? '—' : `${(size / 1024).toFixed(1)} KB`}</dd>
            <dt class="text-gray-400">Access</dt><dd>{accessLabel}</dd>
            {#if revision}<dt class="text-gray-400">Revision</dt><dd>{revision}</dd>{/if}
          </dl>
        {:else}
          <div class="space-y-2">
            {#each revisions as item}
              <button class="block w-full rounded-xl p-3 text-left hover:bg-white/10 {revision === item.number ? 'bg-white/10' : ''}" on:click={() => { onRevisionSelect(item.number); detailPanel = null; }}>
                <span class="block text-sm">Revision {item.number}{item.current ? ' · Current' : ''}</span>
                <span class="text-xs text-gray-400">{new Date(item.created_at).toLocaleString()} · {(item.size / 1024).toFixed(1)} KB</span>
              </button>
            {/each}
          </div>
        {/if}
      </section>
    {/if}
  </div>
  <slot name="action" />
</aside>
