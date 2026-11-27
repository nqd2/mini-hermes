<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	import Plus from '$lib/components/icons/Plus.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Connection from './Terminals/Connection.svelte';
	import AddTerminalServerModal from '$lib/components/AddTerminalServerModal.svelte';

	type TerminalServerConfig = {
		url: string;
		key?: string;
		name?: string;
		path?: string;
		enabled: boolean;
		[key: string]: any;
	};

	export let servers: TerminalServerConfig[] = [];
	export let onChange: (servers: TerminalServerConfig[]) => void = () => {};

	let showAddModal = false;

	const addServer = (server: TerminalServerConfig) => {
		servers = [...servers, server];
		onChange(servers);
	};

	const enableServer = (idx: number) => {
		servers = servers.map((s, i) => ({ ...s, enabled: i === idx }));
		onChange(servers);
	};

	const disableServer = (idx: number) => {
		servers = servers.map((s, i) => (i === idx ? { ...s, enabled: false } : s));
		onChange(servers);
	};

	const updateServer = (idx: number, updated: TerminalServerConfig) => {
		servers = servers.map((s, i) => (i === idx ? updated : s));
		onChange(servers);
	};

	const deleteServer = (idx: number) => {
		servers = servers.filter((_, i) => i !== idx);
		onChange(servers);
	};
</script>

<AddTerminalServerModal
	direct
	bind:show={showAddModal}
	onSubmit={(server: TerminalServerConfig) => addServer(server)}
/>

<div>
	<div class="flex justify-between items-start gap-3 mb-3">
		<div class="min-w-0">
			<h3 id="terminal-connections-heading" class="text-xs font-medium text-gray-700 dark:text-gray-300">
				{$i18n.t('settings.personal.tools.terminals.sections.openTerminal.title')}
			</h3>
			<p class="mt-1 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
				{$i18n.t('settings.personal.tools.terminals.addConnection.description')}
			</p>
		</div>
		<button
			class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08] dark:hover:text-white"
			on:click={() => (showAddModal = true)}
			type="button"
			aria-label={$i18n.t('settings.personal.tools.terminals.addConnection.label')}
		>
			<Plus className="size-3.5" />
			<span>{$i18n.t('Add')}</span>
		</button>
	</div>

	<div class="flex flex-col gap-1.5">
		{#each servers as server, idx}
			<Connection
				bind:connection={server}
				onSubmit={(updated) => updateServer(idx, updated)}
				onDelete={() => deleteServer(idx)}
				onEnable={() => enableServer(idx)}
				onDisable={() => disableServer(idx)}
			/>
		{/each}
	</div>

	{#if servers.length === 0}
		<div class="rounded-lg border border-dashed border-gray-200 px-3 py-2.5 text-xs text-gray-500 dark:border-white/[0.08] dark:text-gray-400">
			{$i18n.t('No terminal connections configured.')}
		</div>
	{/if}
</div>
