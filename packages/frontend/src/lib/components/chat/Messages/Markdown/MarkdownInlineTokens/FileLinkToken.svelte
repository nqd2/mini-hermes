<script context="module" lang="ts">
	type CachedFileMeta = {
		filename: string;
		currentRevision: number | null;
	};
	const fileMetaCache = new Map<string, CachedFileMeta>();
</script>

<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEAI_API_BASE_URL } from '$lib/constants';
	import FileItemModal from '$lib/components/common/FileItemModal.svelte';
	import MaterialFileIcon from '$lib/components/common/MaterialFileIcon.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import Eye from '$lib/components/icons/Eye.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { FileLinkSource } from './fileLinkParser';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');

	export let fileId: string;
	export let source: FileLinkSource = 'agent-runtime-document';
	export let revision: number | null = null;
	export let label: string = '';

	let filename = '';
	let resolvedRevision: number | null = revision;
	let loadingMeta = true;
	let downloading = false;
	let showPreviewModal = false;

	const cleanLabelFilename = (text: string): string => {
		const trimmed = (text || '').trim();
		if (
			!trimmed ||
			trimmed.includes('/api/v1/agent-runtime/documents/') ||
			trimmed.includes('/api/v1/files/')
		) {
			return '';
		}
		return trimmed;
	};

	const getExtension = (name: string): string => {
		const parts = name.split('.');
		return parts.length > 1 ? parts[parts.length - 1].toUpperCase() : 'FILE';
	};

	const parseContentDispositionFilename = (header: string | null): string | null => {
		if (!header) return null;
		const utf8Match = header.match(/filename\*=UTF-8''([^;\n]+)/i);
		if (utf8Match?.[1]) {
			try {
				return decodeURIComponent(utf8Match[1]);
			} catch {
				// fallback below
			}
		}
		const asciiMatch = header.match(/filename="?([^";\n]+)"?/i);
		return asciiMatch?.[1] ?? null;
	};

	onMount(async () => {
		const cacheKey = `${source}:${fileId}`;
		const cached = fileMetaCache.get(cacheKey);
		if (cached) {
			filename = cached.filename;
			resolvedRevision = revision ?? cached.currentRevision;
			loadingMeta = false;
			return;
		}
		try {
			const metaUrl =
				source === 'agent-runtime-document'
					? `${WEAI_API_BASE_URL}/agent-runtime/documents/${encodeURIComponent(fileId)}`
					: `${WEAI_API_BASE_URL}/files/${encodeURIComponent(fileId)}`;
			const res = await fetch(metaUrl, {
				headers: {
					...(localStorage.token ? { Authorization: `Bearer ${localStorage.token}` } : {})
				}
			});
			if (res.ok) {
				const data = await res.json();
				const fetchedName =
					typeof data?.filename === 'string'
						? data.filename
						: typeof data?.meta?.name === 'string'
							? data.meta.name
							: '';
				const fetchedRev =
					typeof data?.current_revision === 'number' ? data.current_revision : null;
				if (fetchedName) {
					filename = fetchedName;
					resolvedRevision = revision ?? fetchedRev;
					fileMetaCache.set(cacheKey, {
						filename: fetchedName,
						currentRevision: fetchedRev
					});
				}
			}
		} catch {
			// fallback to label or generic name
		} finally {
			loadingMeta = false;
		}
	});

	$: displayName = filename || cleanLabelFilename(label) || $i18n.t('File');
	$: ext = getExtension(filename || displayName);
	$: previewItem =
		source === 'agent-runtime-document'
			? {
					id: fileId,
					document_id: fileId,
					name: displayName,
					revision: resolvedRevision ?? revision ?? 1,
					type: 'file'
				}
			: {
					id: fileId,
					name: displayName,
					type: 'file'
				};

	const handleDownload = async () => {
		if (downloading) return;
		downloading = true;
		try {
			let downloadUrl: string;
			if (source === 'agent-runtime-document') {
				const revQuery =
					(resolvedRevision ?? revision) !== null
						? `?revision=${encodeURIComponent(String(resolvedRevision ?? revision))}`
						: '';
				downloadUrl = `${WEAI_API_BASE_URL}/agent-runtime/documents/${encodeURIComponent(fileId)}/content${revQuery}`;
			} else {
				downloadUrl = `${WEAI_API_BASE_URL}/files/${encodeURIComponent(fileId)}/content`;
			}
			const response = await fetch(downloadUrl, {
				headers: {
					...(localStorage.token ? { Authorization: `Bearer ${localStorage.token}` } : {})
				}
			});
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}`);
			}
			const blob = await response.blob();
			const downloadName =
				parseContentDispositionFilename(response.headers.get('content-disposition')) ||
				filename ||
				displayName ||
				`file-${fileId}`;
			const blobUrl = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = blobUrl;
			anchor.download = downloadName;
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(blobUrl);
		} catch {
			toast.error($i18n.t('Failed to download file'));
		} finally {
			downloading = false;
		}
	};
</script>

{#if showPreviewModal}
	<FileItemModal bind:show={showPreviewModal} item={previewItem} />
{/if}

<span
	class="my-1.5 inline-flex max-w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-gray-200 bg-white px-3 py-2 align-middle shadow-2xs dark:border-gray-800 dark:bg-gray-900/80 not-prose"
>
	<span class="flex min-w-0 flex-1 items-center gap-2.5">
		<span
			class="inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-gray-100/80 dark:bg-gray-800/80"
		>
			<MaterialFileIcon filename={filename || displayName} className="size-6" />
		</span>

		<span class="flex min-w-0 flex-1 flex-col">
			<span
				class="truncate text-sm font-medium text-gray-900 dark:text-gray-100"
				title={displayName}
			>
				{#if loadingMeta && !filename && !cleanLabelFilename(label)}
					{$i18n.t('Loading file...')}
				{:else}
					{displayName}
				{/if}
			</span>
			<span class="whitespace-nowrap text-[11px] text-gray-500 dark:text-gray-400">
				{ext}{#if (resolvedRevision ?? revision) !== null} · v{resolvedRevision ?? revision}{/if}
			</span>
		</span>
	</span>

	<span class="flex shrink-0 items-center gap-1.5">
		<button
			type="button"
			class="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
			aria-label={$i18n.t('Preview')}
			on:click|preventDefault|stopPropagation={() => {
				showPreviewModal = true;
			}}
		>
			<Eye className="size-3.5" />
			<span>{$i18n.t('Preview')}</span>
		</button>

		<button
			type="button"
			class="inline-flex items-center gap-1 rounded-lg bg-gray-900 px-2.5 py-1 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			aria-label={$i18n.t('Download')}
			disabled={downloading}
			on:click|preventDefault|stopPropagation={handleDownload}
		>
			{#if downloading}
				<Spinner className="size-3.5" />
			{:else}
				<Download className="size-3.5" strokeWidth="2" />
			{/if}
			<span>{$i18n.t('Download')}</span>
		</button>
	</span>
</span>
