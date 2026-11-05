<script lang="ts">
  import type { WorkBook } from "xlsx";
  import DOMPurify from "dompurify";
  import DocxPreview from "./DocxPreview.svelte";
  import PDFViewer from "./PDFViewer.svelte";
  import PptxPreview from "./PptxPreview.svelte";

  export let filename = "";
  export let data: ArrayBuffer | null = null;
  export let pageCount = 0;
  export let currentPage = 1;
  export let targetPage = 1;
  export let zoom: number | 'fit' = 'fit';

  let workbook: WorkBook | null = null;
  let sheetNames: string[] = [];
  let selectedSheet = "";
  let sheetHtml = "";
  let slides: string[] = [];
  let currentSlide = 0;
  let error = "";
  let loading = false;
  let loadSequence = 0;

  $: extension = filename.toLowerCase().split(".").pop() || "";
  $: if (data) void prepare(data, extension);
  $: if (workbook && selectedSheet) void renderSheet(workbook, selectedSheet);
  $: pageCount = extension === 'pptx' ? slides.length : ['xlsx', 'xls', 'csv'].includes(extension) ? sheetNames.length : 0;
  $: currentPage = extension === 'pptx' ? currentSlide + 1 : Math.max(1, sheetNames.indexOf(selectedSheet) + 1);
  $: if (targetPage > 0 && sheetNames.length && ['xlsx', 'xls', 'csv'].includes(extension)) {
    selectedSheet = sheetNames[Math.min(targetPage - 1, sheetNames.length - 1)];
  }
  $: if (targetPage > 0 && slides.length && extension === 'pptx') {
    currentSlide = Math.min(targetPage - 1, slides.length - 1);
  }

  async function prepare(content: ArrayBuffer, kind: string) {
    const sequence = ++loadSequence;
    workbook = null;
    sheetNames = [];
    sheetHtml = "";
    slides = [];
    error = "";
    if (!["xlsx", "xls", "csv", "pptx"].includes(kind)) return;
    loading = true;
    try {
      if (kind === "pptx") {
        const { pptxToImages } = await import("$lib/utils/pptxToHtml");
        const result = await pptxToImages(content);
        if (sequence === loadSequence) {
          slides = result.images;
          currentSlide = 0;
        }
      } else {
        const { read } = await import("xlsx");
        const parsed = read(content, { type: "array" });
        if (sequence === loadSequence) {
          workbook = parsed;
          sheetNames = parsed.SheetNames;
          selectedSheet = sheetNames[0] || "";
        }
      }
    } catch (cause) {
      if (sequence === loadSequence)
        error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (sequence === loadSequence) loading = false;
    }
  }

  async function renderSheet(book: WorkBook, sheet: string) {
    try {
      const { excelToTable } = await import("$lib/utils/excelToTable");
      const worksheet = book.Sheets[sheet];
      if (!worksheet) return;
      const result = await excelToTable(worksheet);
      if (book === workbook && sheet === selectedSheet)
        sheetHtml = DOMPurify.sanitize(result.html);
    } catch (cause) {
      if (book === workbook && sheet === selectedSheet)
        error = cause instanceof Error ? cause.message : String(cause);
    }
  }
</script>

{#if !data}
  <p class="p-4 text-sm text-gray-500">No document preview is available.</p>
{:else if extension === "docx"}
  <DocxPreview {data} hideControls viewerZoom={zoom} className="h-full min-h-[24rem]" />
{:else if extension === "pdf"}
  <PDFViewer {data} hideControls viewerZoom={zoom} className="h-full min-h-[24rem]" />
{:else if loading}
  <p class="p-4 text-sm text-gray-500">Loading document…</p>
{:else if error}
  <p class="p-4 text-sm text-red-500" role="alert">{error}</p>
{:else if extension === "pptx"}
  {#if slides.length}
    <PptxPreview {slides} bind:currentSlide hideControls hideThumbnailRail viewerZoom={zoom} className="h-full min-h-[24rem]" />
  {:else}
    <p class="p-4 text-sm text-gray-500">No slides are available.</p>
  {/if}
{:else if extension === "xlsx" || extension === "xls" || extension === "csv"}
  <div class="flex h-full min-h-0 flex-col">
    {#if sheetNames.length > 1}
      <nav
        class="flex shrink-0 gap-1 overflow-x-auto border-b border-gray-200 p-2 dark:border-gray-800"
        aria-label="Sheets"
      >
        {#each sheetNames as name}
          <button
            type="button"
            class="rounded-md px-3 py-1.5 text-xs {selectedSheet === name
              ? 'bg-gray-200 dark:bg-gray-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-900'}"
            on:click={() => (selectedSheet = name)}>{name}</button
          >
        {/each}
      </nav>
    {/if}
    <div class="office-preview min-h-0 flex-1 overflow-auto p-3" style:zoom={zoom === 'fit' ? 1 : zoom / 100}>
      {@html sheetHtml}
    </div>
  </div>
{:else}
  <p class="p-4 text-sm text-gray-500">
    This document type has no rendered preview. Download it to view the file.
  </p>
{/if}
