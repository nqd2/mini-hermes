<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	type DocumentItem = {
		id: string;
		filename: string;
		revision: number;
		drive_file_id: string | null;
		sync: { status: string; attempts: number; error: string | null } | null;
	};
	type PreviewItem = {
		id: string;
		status: string;
		diff: string | null;
		error: string | null;
		operations: object[];
		base_revision: number;
		base_sha256: string | null;
		artifact_sha256: string | null;
		patch_hash: string | null;
	};
	let documents: DocumentItem[] = [];
	let selected: DocumentItem | null = null;
	let previews: PreviewItem[] = [];
	let driveUrl = '';
	let findText = '';
	let replaceText = '';
	let sheetName = 'Sheet';
	let cellAddress = 'A1';
	let cellValue = '';
	let statusMessage = '';
	let busy = false;
	let timer: ReturnType<typeof setInterval>;

	async function api(path: string, init: RequestInit = {}) {
		const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
			...init,
			headers: { Authorization: `Bearer ${localStorage.token}`, ...init.headers }
		});
		if (!response.ok) {
			let body: any = {};
			try { body = await response.json(); } catch { /* use status text */ }
			throw new Error(body.detail || response.statusText);
		}
		return response.json();
	}

	async function refresh() {
		try {
			documents = await api('/documents');
			if (selected) {
				selected = documents.find((item) => item.id === selected?.id) || null;
				if (selected) previews = await api(`/documents/${selected.id}/previews`);
			}
		} catch (error) { statusMessage = String(error); }
	}

	async function upload(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		busy = true;
		try {
			const data = new FormData();
			data.append('file', file);
			const item = await api('/documents/upload', { method: 'POST', body: data });
			await refresh();
			selected = documents.find((doc) => doc.id === item.id) || null;
			statusMessage = 'File stored in SeaweedFS.';
		} catch (error) { statusMessage = String(error); }
		finally { busy = false; input.value = ''; }
	}

	async function importDrive() {
		busy = true;
		try {
			const item = await api('/documents/import-drive', {
				method: 'POST', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ url: driveUrl })
			});
			await refresh();
			selected = documents.find((doc) => doc.id === item.id) || null;
			statusMessage = 'Drive file imported.';
		} catch (error) { statusMessage = String(error); }
		finally { busy = false; }
	}

	async function propose() {
		if (!selected) return;
		busy = true;
		try {
			const operations = selected.filename.toLowerCase().endsWith('.xlsx')
				? [{ op: 'set_cell', sheet: sheetName, cell: cellAddress, value: cellValue }]
				: [{ op: 'replace_text', find: findText, replace: replaceText }];
			await api(`/documents/${selected.id}/previews`, {
				method: 'POST', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ operations })
			});
			statusMessage = 'Preview is being prepared.';
			await refresh();
		} catch (error) { statusMessage = String(error); }
		finally { busy = false; }
	}

	async function commit(preview: PreviewItem) {
		busy = true;
		try {
			const approval = preview.base_sha256 && preview.artifact_sha256 && preview.patch_hash
				? preview
				: await api(`/previews/${preview.id}`);
			await api(`/previews/${preview.id}/commit`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					base_revision: approval.base_revision,
					base_sha256: approval.base_sha256,
					artifact_sha256: approval.artifact_sha256,
					patch_hash: approval.patch_hash
				})
			});
			statusMessage = 'Revision committed. Drive sync queued automatically.';
			await refresh();
		} catch (error) { statusMessage = String(error); }
		finally { busy = false; }
	}

	async function retry() {
		if (!selected) return;
		try {
			await api(`/documents/${selected.id}/sync/retry`, { method: 'POST' });
			await refresh();
		} catch (error) { statusMessage = String(error); }
	}

	async function connectDrive() {
		try {
			const result = await api('/drive/connect');
			window.location.href = result.authorization_url;
		} catch (error) { statusMessage = String(error); }
	}

	async function downloadCurrent() {
		if (!selected) return;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes/documents/${selected.id}/content`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) throw new Error(response.statusText);
			const url = URL.createObjectURL(await response.blob());
			const link = document.createElement('a');
			link.href = url;
			link.download = selected.filename;
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) { statusMessage = String(error); }
	}

	async function downloadPreview(previewId: string) {
		if (!selected) return;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes/previews/${previewId}/content`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) throw new Error(response.statusText);
			const url = URL.createObjectURL(await response.blob());
			const link = document.createElement('a');
			link.href = url;
			link.download = `preview-${selected.filename}`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) { statusMessage = String(error); }
	}

	onMount(() => { refresh(); timer = setInterval(refresh, 3000); });
	onDestroy(() => clearInterval(timer));
</script>

<div class="mx-auto max-w-5xl p-6 text-gray-900 dark:text-gray-100">
	<h1 class="text-2xl font-semibold">Documents</h1>
	<p class="mt-1 text-sm text-gray-500">Upload Office or PDF files, or import a Google Docs, Sheets or Slides link. Agent edits appear as previews for your confirmation.</p>
	<div class="mt-6 flex flex-wrap items-center gap-3">
		<label class="cursor-pointer rounded-lg border px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800">
			Upload file <input class="hidden" type="file" accept=".docx,.xlsx,.pptx,.pdf" onchange={upload} disabled={busy} />
		</label>
		<button onclick={connectDrive} class="rounded-lg border px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800">Connect Google Drive</button>
	</div>
	<div class="mt-4 flex gap-2">
		<input class="min-w-0 flex-1 rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Google Docs, Sheets or Slides link" bind:value={driveUrl} />
		<button class="rounded-lg bg-gray-900 px-4 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900" onclick={importDrive} disabled={busy || !driveUrl}>Import</button>
	</div>
	{#if statusMessage}<p class="mt-3 text-sm" role="status">{statusMessage}</p>{/if}
	<div class="mt-8 grid gap-6 md:grid-cols-[240px_1fr]">
		<div>
			<h2 class="mb-2 text-sm font-semibold">Your files</h2>
			{#each documents as doc}
				<button class="mb-1 block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800 {selected?.id === doc.id ? 'bg-gray-100 dark:bg-gray-800' : ''}" onclick={() => { selected = doc; refresh(); }}>
					<span class="block truncate">{doc.filename}</span><span class="text-xs text-gray-500">Revision {doc.revision}</span>
				</button>
			{/each}
		</div>
		{#if selected}
			<div class="space-y-6">
				<div class="rounded-xl border p-4">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<h2 class="font-semibold">{selected.filename}</h2>
						<button class="text-sm underline" onclick={downloadCurrent}>Download current revision</button>
					</div>
					<p class="mt-2 text-sm">Drive sync: {selected.sync?.status || 'No revision committed yet'}</p>
					{#if selected.sync?.error}<p class="mt-1 text-sm text-red-600">{selected.sync.error}</p>{/if}
					{#if selected.sync?.status === 'failed' && !selected.sync?.error?.includes('Drive conflict:')}<button class="mt-2 rounded-lg border px-3 py-1 text-sm" onclick={retry}>Retry sync</button>{/if}
				</div>
				{#if !selected.filename.toLowerCase().endsWith('.pdf')}
					<div class="rounded-xl border p-4">
						<h2 class="font-semibold">Propose an edit</h2>
						{#if selected.filename.toLowerCase().endsWith('.xlsx')}
							<div class="mt-3 grid gap-2 sm:grid-cols-3">
								<input class="rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Sheet name" bind:value={sheetName} />
								<input class="rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Cell (e.g. B2)" bind:value={cellAddress} />
								<input class="rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Value or formula" bind:value={cellValue} />
							</div>
						{:else}
							<div class="mt-3 grid gap-2 sm:grid-cols-2">
								<input class="rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Find" bind:value={findText} />
								<input class="rounded-lg border bg-transparent px-3 py-2 text-sm" placeholder="Replace with" bind:value={replaceText} />
							</div>
						{/if}
						<button class="mt-3 rounded-lg border px-3 py-2 text-sm" onclick={propose} disabled={busy || (selected.filename.toLowerCase().endsWith('.xlsx') ? !sheetName || !cellAddress : !findText)}>Create preview</button>
					</div>
				{/if}
				<div>
					<h2 class="mb-2 font-semibold">Previews</h2>
					{#each previews as preview}
						<div class="mb-3 rounded-xl border p-4">
							<p class="text-sm">{preview.status}</p>
							<pre class="mt-2 overflow-auto text-xs text-gray-500">{JSON.stringify(preview.operations, null, 2)}</pre>
							{#if preview.diff}<pre class="mt-2 max-h-64 overflow-auto rounded bg-gray-100 p-3 text-xs dark:bg-gray-800">{preview.diff}</pre>{/if}
							{#if preview.error}<p class="text-sm text-red-600">{preview.error}</p>{/if}
							{#if preview.status === 'ready' || preview.status === 'committed'}<button class="mt-3 mr-3 text-sm underline" onclick={() => downloadPreview(preview.id)}>Download preview</button>{/if}
							{#if preview.status === 'ready'}<button class="mt-3 rounded-lg bg-gray-900 px-3 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900" onclick={() => commit(preview)} disabled={busy}>Confirm and commit</button>{/if}
						</div>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</div>
