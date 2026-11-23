<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { WEAI_API_BASE_URL } from '$lib/constants';

	import { formatFileSize, safeLinkUrl } from '$lib/utils';
	import { settings, showFileNavPath } from '$lib/stores';

	import FileItemModal from './FileItemModal.svelte';
	import MaterialFileIcon from './MaterialFileIcon.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Spinner from './Spinner.svelte';
	import Tooltip from './Tooltip.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let className = 'w-60';
	export let colorClassName =
		'bg-white dark:bg-gray-850 border border-gray-50/30 dark:border-gray-800/30';
	export let url: string | null = null;

	export let dismissible = false;
	export let modal = false;
	export let loading = false;

	export let item: any = null;
	export let edit = false;
	export let small = false;

	export let name: string;
	export let type: string;
	export let size: number;

	import DocumentPage from '../icons/DocumentPage.svelte';
	import Database from '../icons/Database.svelte';
	import PageEdit from '../icons/PageEdit.svelte';
	import ChatBubble from '../icons/ChatBubble.svelte';
	import Folder from '../icons/Folder.svelte';
	let showModal = false;

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

{#if item}
	<FileItemModal bind:show={showModal} bind:item {edit} />
{/if}

<div class="relative group {className}">
	<button
		class="flex w-full items-center {colorClassName} {small
			? 'h-8 gap-1.5 rounded-xl px-2.5 text-[0.8125rem] leading-5'
			: 'gap-1 rounded-2xl p-1.5'} text-left"
		type="button"
		on:click={async () => {
			const filesystemPath =
				item?.type === 'filesystem' ? (item.path ?? item.url ?? item.id) : null;

			if (filesystemPath) {
				showFileNavPath.set(filesystemPath);
			} else if (item?.file?.data?.content || item?.type === 'file' || item?.content || modal) {
				showModal = !showModal;
			} else {
				if (url) {
					if (type === 'file') {
						if (url.startsWith('http')) {
							window.open(`${url}/content`, '_blank').focus();
						} else {
							window.open(`${WEAI_API_BASE_URL}/files/${url}/content`, '_blank').focus();
						}
					} else if (safeLinkUrl(url)) {
						window.open(`${url}`, '_blank').focus();
					}
				}
			}

			dispatch('click');
		}}
	>
		{#if !small}
			<div
				class="size-10 shrink-0 flex justify-center items-center bg-gray-100/80 dark:bg-white/10 rounded-xl"
			>
				{#if !loading}
					<MaterialFileIcon filename={decodeString(name)} className="size-6" />
				{:else}
					<Spinner />
				{/if}
			</div>
		{:else}
			<div class="shrink-0 text-gray-500 dark:text-gray-400">
				{#if !loading}
					<Tooltip
						content={type === 'collection'
							? $i18n.t('Collection')
							: type === 'note'
								? $i18n.t('Note')
								: type === 'chat'
									? $i18n.t('Chat')
									: type === 'file' || type === 'filesystem'
										? $i18n.t('File')
										: $i18n.t('Document')}
						placement="top"
					>
						{#if type === 'collection'}
							<Database className="size-3.5" />
						{:else if type === 'note'}
							<PageEdit className="size-3.5" />
						{:else if type === 'chat'}
							<ChatBubble className="size-3.5" />
						{:else if type === 'folder'}
							<Folder className="size-3.5" />
						{:else}
							<MaterialFileIcon filename={decodeString(name)} className="size-4" />
						{/if}
					</Tooltip>
				{:else}
					<Spinner className="size-3.5" />
				{/if}
			</div>
		{/if}

		{#if !small}
			<div class="flex flex-col justify-center -space-y-0.5 px-2.5 w-full">
				<div class=" dark:text-gray-100 text-sm font-normal line-clamp-1 mb-1">
					{decodeString(name)}
				</div>

				<div
					class=" flex justify-between text-xs line-clamp-1 {($settings?.highContrastMode ?? false)
						? 'text-gray-800 dark:text-gray-100'
						: 'text-gray-500'}"
				>
					{#if type === 'file' || type === 'filesystem'}
						{$i18n.t('File')}
					{:else if type === 'note'}
						{$i18n.t('Note')}
					{:else if type === 'doc'}
						{$i18n.t('Document')}
					{:else if type === 'collection'}
						{$i18n.t('Collection')}
					{:else}
						<span class=" capitalize line-clamp-1">{type}</span>
					{/if}
					{#if size}
						<span class="capitalize">{formatFileSize(size)}</span>
					{/if}
				</div>
			</div>
		{:else}
			<Tooltip
				content={decodeString(name)}
				className="flex min-w-0 flex-1 overflow-hidden"
				placement="top-start"
			>
				<div class="flex min-w-0 flex-1 items-center overflow-hidden">
					<div class="flex min-w-0 flex-1 items-center justify-between dark:text-gray-100">
						<div class="min-w-0 flex-1 truncate pr-1 font-normal">{decodeString(name)}</div>
						{#if size}
							<div class="max-w-[35%] shrink-0 truncate text-[0.6875rem] capitalize text-gray-500">
								{formatFileSize(size)}
							</div>
						{:else}
							<div class="max-w-[35%] shrink-0 truncate text-[0.6875rem] capitalize text-gray-500">
								{type}
							</div>
						{/if}
					</div>
				</div>
			</Tooltip>
		{/if}
	</button>

	{#if dismissible}
		<div class=" absolute -top-[3px] -right-[3px] flex">
			<button
				aria-label={$i18n.t('Remove File')}
				class=" bg-white text-black border border-gray-50 rounded-full {($settings?.highContrastMode ??
				false)
					? ''
					: 'hover-reveal transition'}"
				type="button"
				on:click|stopPropagation={() => {
					dispatch('dismiss');
				}}
			>
				<XMark className={'size-4'} />
			</button>

			<!-- <button
				class=" p-1 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-full group-hover:visible invisible transition"
				type="button"
				on:click={() => {
				}}
			>
				<GarbageBin />
			</button> -->
		</div>
	{/if}
</div>
