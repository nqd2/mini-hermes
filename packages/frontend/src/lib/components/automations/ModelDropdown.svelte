<script lang="ts">
	import { getContext } from 'svelte';

	import { models } from '$lib/stores';
	import { WEAI_API_BASE_URL } from '$lib/constants';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import ModelIcon from '$lib/components/common/ModelIcon.svelte';

	const i18n = getContext('i18n');

	export let model_id = '';

	export let side: 'top' | 'bottom' = 'top';
	export let align: 'start' | 'end' = 'start';

	/** Optional callback when selection changes */
	export let onChange: () => void = () => {};

	let showDropdown = false;
	let modelSearch = '';

	$: modelLabel = model_id
		? $models.find((m) => m.id === model_id)?.name || model_id
		: $i18n.t('Select model');

	$: filteredModels = (
		modelSearch
			? $models.filter(
					(m) =>
						m.name.toLowerCase().includes(modelSearch.toLowerCase()) ||
						m.id.toLowerCase().includes(modelSearch.toLowerCase())
				)
			: $models
	).filter((m) => !(m?.info?.meta?.hidden ?? false));
</script>

<Dropdown bind:show={showDropdown} {side} {align}>
	<button
		type="button"
		class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-2xl text-xs transition
			text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
	>
		<ModelIcon modelId={model_id} className="size-4 rounded-full" />
		<span class="whitespace-nowrap max-w-32 truncate">{modelLabel}</span>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 24 24"
			stroke-width="2"
			stroke="currentColor"
			class="size-2.5"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
		</svg>
	</button>

	<div
		slot="content"
		class="rounded-xl shadow-lg border border-gray-200 dark:border-gray-800 flex flex-col bg-white dark:bg-gray-850 w-72 p-0.5"
	>
		<div class="flex items-center gap-1.5 px-2 py-1">
			<Search className="size-3.5" strokeWidth="2.5" />
			<input
				bind:value={modelSearch}
				class="w-full text-[0.8125rem] bg-transparent outline-hidden"
				placeholder={$i18n.t('Search a model')}
				autocomplete="off"
				on:click={(e) => e.stopPropagation()}
			/>
		</div>

		<div class="overflow-y-auto scrollbar-thin max-h-60">
			<div class="px-2 text-[0.6875rem] text-gray-500 py-0.5">
				{$i18n.t('Models')}
			</div>

			{#each filteredModels as model (model.id)}
				<button
					class="h-[1.6875rem] px-2 rounded-xl w-full text-left text-[0.8125rem] {model_id ===
					model.id
						? 'text-gray-900 dark:text-gray-100'
						: 'text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100'}"
					type="button"
					on:click={() => {
						model_id = model.id;
						showDropdown = false;
						modelSearch = '';
						onChange();
					}}
				>
					<div class="flex items-center text-black dark:text-gray-100 line-clamp-1">
						<ModelIcon
							modelId={model.id}
							alt={model?.name ?? model.id}
							className="rounded-full size-5 mr-2"
						/>
						<div class="truncate">
							{model.name}
						</div>
					</div>
				</button>
			{:else}
				<div class="block px-2 py-1.5 text-[0.8125rem] text-gray-700 dark:text-gray-100">
					{$i18n.t('No results found')}
				</div>
			{/each}
		</div>
	</div>
</Dropdown>
