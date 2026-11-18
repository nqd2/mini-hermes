<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getRouting, saveRouting, type Capability, type PoolCandidate, type RoutingSettings } from '$lib/apis/model-routing/tiers';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');
	const capabilities: Capability[] = ['text', 'image', 'tools', 'structured_output', 'responses'];
	const capabilityLabels: Record<Capability, string> = {
		text: 'Text', image: 'Images', tools: 'Tools', structured_output: 'Structured output', responses: 'Responses API'
	};
	const inputClass = 'rounded-lg border border-gray-200 bg-transparent px-2 py-1 dark:border-gray-700';
	let routing: RoutingSettings | null = null;
	let error = '';
	let saving = false;

	async function load() {
		try {
			routing = await getRouting(localStorage.token);
			error = '';
		} catch {
			error = $i18n.t('Could not load model routing');
		}
	}

	onMount(load);

	function addCandidate(group: string) {
		if (!routing) return;
		const connection = routing.connections.find((item) => item.enabled);
		if (!connection) return;
		const candidate: PoolCandidate = {
			connection_id: connection.id, model_id: '', priority: 0,
			min_context: 8192, capabilities: ['text'], enabled: true
		};
		routing.config.groups[group] = [...routing.config.groups[group], candidate];
	}

	async function save() {
		if (!routing || saving) return;
		saving = true;
		error = '';
		try {
			routing.config = await saveRouting(localStorage.token, routing.config);
			toast.success($i18n.t('Model routing saved'));
		} catch (failure) {
			const messages: Record<string, string> = {
				invalid_model_connection: 'Choose an active provider connection for each enabled model.',
				invalid_free_model: 'Free groups require an approved OpenRouter model with verified zero pricing.',
				model_pool_changed: 'An administrator updated the model groups. Reload to get the latest settings.'
			};
			error = $i18n.t(messages[(failure as Error).message] ?? 'Could not save model routing');
		} finally {
			saving = false;
		}
	}
</script>

<details class="mb-4 rounded-xl border border-gray-200 p-3 text-sm dark:border-gray-800">
	<summary class="cursor-pointer font-medium">{$i18n.t('Tier model routing')}</summary>
	{#if error}
		<p class="my-2 text-red-600 dark:text-red-400" role="alert">{error}</p>
	{/if}
	{#if routing}
		<form class="mt-3 flex flex-col gap-3" on:submit|preventDefault={save}>
			<fieldset disabled={saving} class="flex flex-wrap gap-3">
				<label class="flex flex-col text-xs">
					{$i18n.t('Free requests per user per day')}
					<input type="number" class={inputClass} min="1" required bind:value={routing.config.free_user_daily_limit} />
				</label>
				<label class="flex flex-col text-xs">
					{$i18n.t('Shared Free requests per day')}
					<input type="number" class={inputClass} min="1" required bind:value={routing.config.free_account_daily_limit} />
				</label>
				<label class="flex flex-col text-xs">
					{$i18n.t('Shared Free requests per minute')}
					<input type="number" class={inputClass} min="1" required bind:value={routing.config.free_account_minute_limit} />
				</label>
			</fieldset>
			{#each Object.keys(routing.config.groups) as group (group)}
				<fieldset disabled={saving} class="flex flex-col gap-2 rounded-lg border border-gray-200 p-2 dark:border-gray-800">
					<legend class="px-1 text-xs font-medium capitalize">{group.slice(3).replace('-', ' ')}</legend>
					{#each routing.config.groups[group] as candidate, index}
						<div class="flex flex-wrap items-end gap-2 text-xs">
							<label class="flex min-w-36 flex-col">
								{$i18n.t('Connection')}
								<select class={inputClass} bind:value={candidate.connection_id} required>
									{#if !routing.connections.some((connection) => connection.id === candidate.connection_id)}
										<option value={candidate.connection_id}>{$i18n.t('Unavailable connection')}</option>
									{/if}
									{#each routing.connections as connection (connection.id)}
										<option value={connection.id}>{connection.url} · {connection.id.slice(0, 8)}</option>
									{/each}
								</select>
							</label>
							<label class="flex flex-col">
								{$i18n.t('Model ID')}
								<input class={inputClass} bind:value={candidate.model_id} required />
							</label>
							<label class="flex w-20 flex-col">
								{$i18n.t('Priority')}
								<input type="number" class={inputClass} min="0" required bind:value={candidate.priority} />
							</label>
							<label class="flex w-28 flex-col">
								{$i18n.t('Context tokens')}
								<input type="number" class={inputClass} min="1" required bind:value={candidate.min_context} />
							</label>
							<label class="pb-1">
								<input type="checkbox" bind:checked={candidate.enabled} />
								{$i18n.t('Enabled')}
							</label>
							<button type="button" class="pb-1 text-red-600 dark:text-red-400"
								on:click={() => {
									routing.config.groups[group] = routing.config.groups[group].filter((_, i) => i !== index);
								}}>{$i18n.t('Remove')}</button>
							<div class="flex w-full flex-wrap gap-2">
								{#each capabilities as capability (capability)}
									<label>
										<input type="checkbox" bind:group={candidate.capabilities} value={capability} disabled={capability === 'text'} />
										{$i18n.t(capabilityLabels[capability])}
									</label>
								{/each}
							</div>
						</div>
					{/each}
					<button type="button" class="self-start text-xs underline disabled:opacity-50"
						disabled={!routing.connections.some((item) => item.enabled)} on:click={() => addCandidate(group)}>
						{$i18n.t('Add model')}
					</button>
				</fieldset>
			{/each}
			<div class="flex gap-3">
				<button type="submit" class={inputClass} disabled={saving}>{$i18n.t('Save routing')}</button>
				{#if error}
					<button type="button" class="text-xs underline" on:click={load}>{$i18n.t('Reload')}</button>
				{/if}
			</div>
		</form>
	{:else if error}
		<button type="button" class="mt-2 text-xs underline" on:click={load}>{$i18n.t('Retry')}</button>
	{/if}
</details>
