<script lang="ts">
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { settings } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Connection from './Connections/Connection.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	import AddConnectionModal from '$lib/components/AddConnectionModal.svelte';

	export let saveSettings: Function;

	let config = null;

	let showConnectionModal = false;

	const addConnectionHandler = async (connection) => {
		config.OPENAI_API_BASE_URLS.push(connection.url);
		config.OPENAI_API_KEYS.push(connection.key);
		config.OPENAI_API_CONFIGS[config.OPENAI_API_BASE_URLS.length - 1] = connection.config;

		await updateHandler();
	};

	const updateHandler = async () => {
		// Remove trailing slashes
		config.OPENAI_API_BASE_URLS = config.OPENAI_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		// Check if API KEYS length is same than API URLS length
		if (config.OPENAI_API_KEYS.length !== config.OPENAI_API_BASE_URLS.length) {
			// if there are more keys than urls, remove the extra keys
			if (config.OPENAI_API_KEYS.length > config.OPENAI_API_BASE_URLS.length) {
				config.OPENAI_API_KEYS = config.OPENAI_API_KEYS.slice(
					0,
					config.OPENAI_API_BASE_URLS.length
				);
			}

			// if there are more urls than keys, add empty keys
			if (config.OPENAI_API_KEYS.length < config.OPENAI_API_BASE_URLS.length) {
				const diff = config.OPENAI_API_BASE_URLS.length - config.OPENAI_API_KEYS.length;
				for (let i = 0; i < diff; i++) {
					config.OPENAI_API_KEYS.push('');
				}
			}
		}

		await saveSettings({
			directConnections: config
		});
	};

	onMount(async () => {
		config = $settings?.directConnections ?? {
			OPENAI_API_BASE_URLS: [],
			OPENAI_API_KEYS: [],
			OPENAI_API_CONFIGS: {}
		};
	});
</script>

<AddConnectionModal direct bind:show={showConnectionModal} onSubmit={addConnectionHandler} />

<form
	id="tab-connections"
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => {
		updateHandler();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.connections.title')}
	</h2>

	<div class="flex flex-1 min-h-0 flex-col overflow-y-auto scrollbar-hover pr-1.5">
		{#if config !== null}
			<UserSettingSection first>
				<div class="flex items-start justify-between gap-3">
					<div class="min-w-0">
						<h3 id="direct-connections-heading" class="text-xs font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('settings.personal.connections.sections.directConnections.title')}
						</h3>
						<p class="mt-1 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
							{$i18n.t('settings.personal.connections.addConnection.description')}
						</p>
					</div>

					<button
						class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08] dark:hover:text-white"
						aria-label={$i18n.t('settings.personal.connections.addConnection.label')}
						on:click={() => {
							showConnectionModal = true;
						}}
						type="button"
					>
						<Plus className="size-3.5" />
						<span>{$i18n.t('Add')}</span>
					</button>
				</div>

				<div class="flex flex-col gap-2">
					{#each config?.OPENAI_API_BASE_URLS ?? [] as url, idx}
						<Connection
							bind:url
							bind:key={config.OPENAI_API_KEYS[idx]}
							bind:config={config.OPENAI_API_CONFIGS[idx]}
							onSubmit={() => {
								updateHandler();
							}}
							onDelete={() => {
								config.OPENAI_API_BASE_URLS = config.OPENAI_API_BASE_URLS.filter(
									(url, urlIdx) => idx !== urlIdx
								);
								config.OPENAI_API_KEYS = config.OPENAI_API_KEYS.filter(
									(key, keyIdx) => idx !== keyIdx
								);

								let newConfig = {};
								config.OPENAI_API_BASE_URLS.forEach((url, newIdx) => {
									newConfig[newIdx] = config.OPENAI_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
								});
								config.OPENAI_API_CONFIGS = newConfig;
								updateHandler();
							}}
						/>
					{/each}
				</div>

				{#if (config?.OPENAI_API_BASE_URLS ?? []).length === 0}
					<div class="rounded-lg border border-dashed border-gray-200 px-3 py-2.5 text-xs text-gray-500 dark:border-white/[0.08] dark:text-gray-400">
						{$i18n.t('No direct connections configured.')}
					</div>
				{/if}
			</UserSettingSection>

			<div class="mt-6">
				<div
					class="space-y-2 border-t border-gray-200/70 pt-4 text-[0.6875rem] leading-relaxed text-gray-500 dark:border-white/[0.06] dark:text-gray-400"
				>
					<p>
						{$i18n.t(
							'Connections managed through the Admin Panel are recommended for everyday use. Direct connections provide convenient access to your own endpoints, but rely on your browser session to keep requests running, making them better suited to testing and temporary use.'
						)}
					</p>
					<p>
						{$i18n.t(
							'CORS must be properly configured by the provider to allow requests from WeAI.'
						)}
					</p>
				</div>
			</div>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner variant="brand" />
				</div>
			</div>
		{/if}
	</div>

	<div class="shrink-0 flex justify-end pt-3 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
