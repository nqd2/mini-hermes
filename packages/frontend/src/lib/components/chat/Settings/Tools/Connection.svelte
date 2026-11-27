<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	const settingId = `connection-${uuidv4()}`;
	import { getContext, tick } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import AddToolServerModal from '$lib/components/AddToolServerModal.svelte';
	import WrenchAlt from '$lib/components/icons/WrenchAlt.svelte';

	export let onDelete = () => {};
	export let onSubmit = () => {};

	export let connection = null;
	export let direct = false;

	let showConfigModal = false;
</script>

<AddToolServerModal
	edit
	{direct}
	bind:show={showConfigModal}
	{connection}
	onDelete={() => {
		onDelete();
		showConfigModal = false;
	}}
	onSubmit={(c) => {
		connection = c;
		onSubmit(c);
	}}
/>

<div
	class="group/setting-row flex w-full items-center gap-3 rounded-lg border border-gray-200/70 bg-gray-50/50 px-2.5 py-2 text-xs text-gray-700 transition hover:border-gray-300/80 dark:border-white/[0.06] dark:bg-white/[0.02] dark:text-gray-300 dark:hover:border-white/[0.12]"
>
	<Tooltip className="w-full relative min-w-0" content={''} placement="top-start">
		<div class="flex w-full min-w-0">
			<div
				class="flex-1 min-w-0 relative flex gap-2 items-center {!(connection?.config?.enable ?? true)
					? 'opacity-50'
					: ''}"
			>
				<Tooltip
					content={connection?.type === 'mcp'
						? $i18n.t('settings.admin.integrations.mcp.label')
						: direct
							? $i18n.t('settings.personal.tools.openApi.label')
							: $i18n.t('settings.admin.integrations.openApi.label')}
				>
					<WrenchAlt className="size-4 shrink-0 text-gray-500 dark:text-gray-400" />
				</Tooltip>

				<div class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
					{#if connection?.info?.name}
						<label
							for={`${settingId}-control`}
							class="truncate font-medium text-gray-800 dark:text-gray-200 transition-colors duration-150 group-hover/setting-row:text-gray-900 dark:group-hover/setting-row:text-white group-has-[button[id$=-control]:enabled]/setting-row:cursor-pointer group-has-[button[id$=-control]:disabled]/setting-row:cursor-not-allowed"
						>
							{connection?.info?.name ?? connection?.url}
						</label>
						{#if connection?.info?.id}
							<span class="truncate text-[11px] text-gray-500 dark:text-gray-400">
								{connection.info.id}
							</span>
						{/if}
					{:else}
						<label
							for={`${settingId}-control`}
							class="truncate font-medium text-gray-800 dark:text-gray-200 transition-colors duration-150 group-hover/setting-row:text-gray-900 dark:group-hover/setting-row:text-white group-has-[button[id$=-control]:enabled]/setting-row:cursor-pointer group-has-[button[id$=-control]:disabled]/setting-row:cursor-not-allowed"
						>
							{connection?.url}
						</label>
					{/if}

					<span
						class="shrink-0 rounded-full bg-gray-200/70 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-gray-600 dark:bg-white/[0.08] dark:text-gray-300"
					>
						{connection?.type === 'mcp' ? 'MCP' : 'OpenAPI'}
					</span>

					{#if connection?.auth_type === 'oauth_2.1' || connection?.auth_type === 'oauth_2.1_static'}
						<span
							class="shrink-0 rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
						>
							OAuth 2.1
						</span>
					{/if}
				</div>
			</div>
		</div>
	</Tooltip>

	<div class="flex shrink-0 items-center gap-1.5">
		<Tooltip content={$i18n.t('Configure')} className="self-start">
			<button
				aria-label={$i18n.t('Configure')}
				class="flex size-7 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-black/5 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/[0.08] dark:hover:text-white"
				on:click={() => {
					showConfigModal = true;
				}}
				type="button"
			>
				<Cog6 />
			</button>
		</Tooltip>

		<Tooltip
			content={(connection?.config?.enable ?? true) ? $i18n.t('Enabled') : $i18n.t('Disabled')}
		>
			<Switch
				id={`${settingId}-control`}
				state={connection?.config?.enable ?? true}
				on:change={() => {
					if (!connection.config) connection.config = {};
					connection.config.enable = !(connection?.config?.enable ?? true);
					onSubmit(connection);
				}}
			/>
		</Tooltip>
	</div>
</div>
