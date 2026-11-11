<script lang="ts">
	import { onMount } from 'svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import DocumentViewerShell from '$lib/components/common/DocumentViewerShell.svelte';
	import OfficeDocumentPreview from '$lib/components/common/OfficeDocumentPreview.svelte';

	export let chatId = '';
	export let files: any[] = [];

	type FolderItem = { id: string; parent_id: string | null; name: string; is_personal_root: boolean; can_write: boolean };
	type DocumentItem = {
		id: string;
		filename: string;
		folder_id: string | null;
		description: string | null;
		description_status: string;
		revision: number;
		can_write: boolean;
		in_chat: boolean;
	};

	let folders: FolderItem[] = [];
	let documents: DocumentItem[] = [];
	let selected = new Set<string>();
	let rootFolderId = '';
	let newFolderName = '';
	let query = '';
	let busy = true;
	let error = '';
	let viewingDocument: DocumentItem | null = null;
	let viewerData: ArrayBuffer | null = null;
	let viewerRevision = 0;
	let viewerError = '';
	let viewerZoom: number | 'fit' = 'fit';
	let viewerPageCount = 0;
	let viewerCurrentPage = 1;
	let viewerTargetPage = 1;

	const openDocument = async (document: DocumentItem, revision = document.revision) => {
		viewingDocument = document;
		viewerRevision = revision;
		viewerData = null;
		viewerError = '';
		viewerTargetPage = 1;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes/documents/${encodeURIComponent(document.id)}/content?revision=${revision}`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) throw new Error('Could not load this document');
			const data = await response.arrayBuffer();
			if (viewingDocument?.id === document.id && viewerRevision === revision) viewerData = data;
		} catch (cause) {
			viewerError = cause instanceof Error ? cause.message : String(cause);
		}
	};

	const downloadDocument = () => {
		if (!viewerData || !viewingDocument) return;
		const url = URL.createObjectURL(new Blob([viewerData]));
		const link = document.createElement('a');
		link.href = url;
		link.download = viewingDocument.filename;
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	};

	const api = async (path: string, init: RequestInit = {}) => {
		const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes${path}`, {
			...init,
			headers: {
				Authorization: `Bearer ${localStorage.token}`,
				...(init.body ? { 'Content-Type': 'application/json' } : {}),
				...init.headers
			}
		});
		if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
		return response.json();
	};

	const refresh = async () => {
		busy = true;
		error = '';
		try {
			if (!chatId || chatId.startsWith('temporary:') || chatId.startsWith('local:')) {
				throw new Error('Send the first message to create this chat, then use /files to attach library documents.');
			}
			const fileIds = [...new Set((files || [])
				.filter((file) => file?.type !== 'filesystem' && file?.id)
				.map((file) => String(file.id)))];
			if (fileIds.length) {
				await api(`/chats/${chatId}/files/attachments`, {
					method: 'PUT', body: JSON.stringify({ file_ids: fileIds })
				});
			}
			const result = await api(`/personal/files?chat_id=${encodeURIComponent(chatId)}`);
			folders = result.folders || [];
			rootFolderId = result.root_folder_id;
			documents = result.documents || [];
			selected = new Set(documents.filter((document) => document.in_chat).map((document) => document.id));
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	};

	const createFolder = async () => {
		if (!newFolderName.trim()) return;
		busy = true;
		error = '';
		try {
			await api('/folders', { method: 'POST', body: JSON.stringify({ name: newFolderName.trim(), parent_id: rootFolderId }) });
			newFolderName = '';
			await refresh();
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
			busy = false;
		}
	};

	const moveDocument = async (documentId: string, folderId: string) => {
		try {
			await api(`/documents/${documentId}/folder`, {
				method: 'PUT', body: JSON.stringify({ folder_id: folderId || null })
			});
			documents = documents.map((document) => document.id === documentId ? { ...document, folder_id: folderId || null } : document);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		}
	};

	const retryDescription = async (documentId: string) => {
		try {
			await api(`/documents/${documentId}/description/retry`, { method: 'POST' });
			documents = documents.map((document) => document.id === documentId
				? { ...document, description_status: 'pending', description: null }
				: document);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		}
	};

	const persist = async (next: Set<string>) => {
		selected = next;
		busy = true;
		error = '';
		try {
			await api(`/chats/${chatId}/files`, {
				method: 'PUT', body: JSON.stringify({ document_ids: [...selected], folder_ids: [] })
			});
			documents = documents.map((document) => ({ ...document, in_chat: selected.has(document.id) }));
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
			await refresh();
		} finally {
			busy = false;
		}
	};

	const descendants = (folderId: string) => {
		const ids = new Set([folderId]);
		let changed = true;
		while (changed) {
			const children = folders.filter((folder) => folder.parent_id && ids.has(folder.parent_id));
			changed = false;
			for (const child of children) if (!ids.has(child.id)) { ids.add(child.id); changed = true; }
		}
		return documents.filter((document) => document.folder_id && ids.has(document.folder_id));
	};

	const toggleDocument = (documentId: string) => {
		const next = new Set(selected);
		if (next.has(documentId)) next.delete(documentId);
		else next.add(documentId);
		void persist(next);
	};

	const toggleFolder = (folderId: string) => {
		const descendantsInFolder = descendants(folderId);
		const next = new Set(selected);
		const allSelected = descendantsInFolder.length > 0 && descendantsInFolder.every((document) => next.has(document.id));
		for (const document of descendantsInFolder) {
			if (allSelected) next.delete(document.id);
			else next.add(document.id);
		}
		void persist(next);
	};

	$: normalizedQuery = query.trim().toLowerCase();
	$: visibleFolders = folders.filter((folder) => !normalizedQuery || folder.name.toLowerCase().includes(normalizedQuery));
	$: visibleDocuments = documents.filter((document) => !normalizedQuery ||
		`${document.filename} ${document.description || ''}`.toLowerCase().includes(normalizedQuery));

	onMount(refresh);
</script>

<div class="flex min-h-0 flex-1 flex-col overflow-hidden">
	<div class="border-b border-gray-100 px-3 py-2 dark:border-gray-800">
		<input bind:value={query} class="w-full rounded-lg border bg-transparent px-2.5 py-2 text-xs" placeholder="Search your Personal Folder" />
		<p class="mt-1 text-[0.7rem] text-gray-500">Selected files are available to the agent in this chat only.</p>
		<div class="mt-2 flex gap-1.5">
			<input bind:value={newFolderName} class="min-w-0 flex-1 rounded-lg border bg-transparent px-2 py-1.5 text-xs" placeholder="New folder name" />
			<button class="rounded-lg border px-2 py-1.5 text-xs" type="button" disabled={busy || !newFolderName.trim()} on:click={createFolder}>Create folder</button>
		</div>
	</div>
	{#if error}<p class="px-3 py-2 text-xs text-red-500" role="alert">{error}</p>{/if}
	{#if busy && !documents.length}
		<div class="flex justify-center py-8"><Spinner /></div>
	{:else if !busy && !error && !documents.length}
		<p class="px-3 py-8 text-center text-xs text-gray-500">No Office documents in your Personal Folder yet.</p>
	{:else}
		<div class="min-h-0 flex-1 overflow-y-auto p-2">
			{#each visibleFolders as folder (folder.id)}
				{@const inFolder = descendants(folder.id)}
				{#if inFolder.length}
					<label class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-xs hover:bg-gray-50 dark:hover:bg-gray-800">
						<input type="checkbox" checked={inFolder.every((document) => selected.has(document.id))} on:change={() => toggleFolder(folder.id)} />
						<span class="truncate">📁 {folder.name}</span><span class="ml-auto text-gray-500">{inFolder.length}</span>
					</label>
				{/if}
			{/each}
			{#each visibleDocuments as document (document.id)}
				<div class="rounded-lg px-2 py-2 text-xs hover:bg-gray-50 dark:hover:bg-gray-800">
					<label class="flex cursor-pointer items-start gap-2">
						<input type="checkbox" checked={selected.has(document.id)} on:change={() => toggleDocument(document.id)} />
						<span class="min-w-0 flex-1">
							<span class="block truncate">{document.filename}</span>
							{#if document.description}<span class="mt-0.5 block line-clamp-2 text-[0.68rem] text-gray-500">{document.description}</span>{/if}
				{#if ['failed', 'failed_permanent'].includes(document.description_status) && document.can_write}
								<button class="mt-1 text-[0.68rem] text-sky-600 hover:underline" type="button" on:click={() => retryDescription(document.id)}>Retry description</button>
							{/if}
						</span>
					</label>
					<button class="ml-5 mt-1 text-[0.68rem] text-sky-600 hover:underline" type="button" on:click={() => openDocument(document)}>View file</button>
					<select class="mt-2 ml-5 max-w-[calc(100%-1.5rem)] rounded-md border bg-transparent px-1.5 py-1 text-[0.68rem]" value={document.folder_id || rootFolderId} on:change={(event) => moveDocument(document.id, event.currentTarget.value)} aria-label={`Move ${document.filename}`}>
						{#each folders.filter((folder) => folder.can_write) as folder (folder.id)}
							<option value={folder.id}>{folder.is_personal_root ? 'Personal' : folder.name}</option>
						{/each}
					</select>
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if viewingDocument}
	<DocumentViewerShell
		filename={viewingDocument.filename}
		documentId={viewingDocument.id}
		revision={viewerRevision}
		size={viewerData?.byteLength ?? null}
		location={folders.find((folder) => folder.id === viewingDocument?.folder_id)?.name || 'Personal Folder'}
		accessLabel={viewingDocument.can_write ? 'Can edit' : 'Can view'}
		bind:zoom={viewerZoom}
		onClose={() => (viewingDocument = null)}
		onDownload={downloadDocument}
		onRevisionSelect={(number) => viewingDocument && openDocument(viewingDocument, number)}
		pageCountOverride={viewerPageCount}
		currentPageOverride={viewerCurrentPage}
		onPageSelect={(page) => (viewerTargetPage = page)}
	>
		{#if viewerError}<p role="alert" class="p-4 text-sm text-red-400">{viewerError}</p>
		{:else if viewerData}
			<OfficeDocumentPreview filename={viewingDocument.filename} data={viewerData} bind:pageCount={viewerPageCount} bind:currentPage={viewerCurrentPage} targetPage={viewerTargetPage} zoom={viewerZoom} />
		{:else}<div class="p-4 text-sm text-gray-400">Loading document…</div>{/if}
	</DocumentViewerShell>
{/if}
