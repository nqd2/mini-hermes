<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getFreeCatalog,
		getRouting,
		saveRouting,
		type Capability,
		type FreeCatalogModel,
		type PoolCandidate,
		type RoutingSettings,
		type TierScope
	} from '$lib/apis/model-routing/tiers';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');
	const capabilities: Capability[] = ['text', 'image', 'tools', 'structured_output', 'responses'];
	const capabilityLabels: Record<Capability, string> = {
		text: 'Text',
		image: 'Images',
		tools: 'Tools',
		structured_output: 'Structured output',
		responses: 'Responses API'
	};
	const freeGroupColumns = [
		{ id: 'agent-runtime-free-small', label: 'Low (Small)' },
		{ id: 'agent-runtime-free-medium', label: 'Medium' },
		{ id: 'agent-runtime-free-large', label: 'Premium (Large)' }
	] as const;
	const groupLabels: Record<string, string> = {
		'agent-runtime-free-small': 'Free — Low (Small)',
		'agent-runtime-free-medium': 'Free — Medium',
		'agent-runtime-free-large': 'Free — Premium (Large)',
		'agent-runtime-plus-small': 'Plus — Small',
		'agent-runtime-plus-medium': 'Plus — Medium',
		'agent-runtime-plus-large': 'Plus — Large'
	};
	const inputClass = 'rounded-lg border border-gray-200 bg-transparent px-2 py-1 dark:border-gray-700';

	let routing: RoutingSettings | null = null;
	let freeCatalog: FreeCatalogModel[] = [];
	let loadingCatalog = false;
	let catalogError = '';
	let catalogQuery = '';
	let error = '';
	let saving = false;

	async function loadCatalog(refresh = false) {
		loadingCatalog = true;
		catalogError = '';
		try {
			const res = await getFreeCatalog(localStorage.token, refresh);
			freeCatalog = res.models ?? [];
		} catch {
			catalogError = $i18n.t('Could not load :free model catalog');
		} finally {
			loadingCatalog = false;
		}
	}

	async function load() {
		try {
			routing = await getRouting(localStorage.token);
			error = '';
			void loadCatalog(false);
		} catch {
			error = $i18n.t('Could not load model routing');
		}
	}

	onMount(load);

	$: filteredFreeCatalog = catalogQuery.trim()
		? freeCatalog.filter((item) => {
				const q = catalogQuery.trim().toLowerCase();
				return item.model_id.toLowerCase().includes(q) || item.name.toLowerCase().includes(q);
			})
		: freeCatalog;

	function formatContext(tokens: number): string {
		if (tokens >= 1_000_000) return `${Math.round(tokens / 100_000) / 10}M`;
		if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
		return `${tokens}`;
	}

	function isAssigned(group: string, item: FreeCatalogModel): boolean {
		const list = routing?.config.groups[group] ?? [];
		return list.some(
			(candidate) =>
				candidate.connection_id === item.connection_id && candidate.model_id === item.model_id
		);
	}

	function toggleAssigned(group: string, item: FreeCatalogModel, checked: boolean) {
		if (!routing) return;
		const current = routing.config.groups[group] ?? [];
		if (checked) {
			if (
				!current.some(
					(candidate) =>
						candidate.connection_id === item.connection_id &&
						candidate.model_id === item.model_id
				)
			) {
				const candidate: PoolCandidate = {
					connection_id: item.connection_id,
					model_id: item.model_id,
					priority: 0,
					min_context: Math.max(1, item.context_length || 8192),
					capabilities: item.capabilities.includes('text')
						? [...item.capabilities]
						: ['text', ...item.capabilities],
					enabled: true
				};
				routing.config.groups[group] = [...current, candidate];
			}
		} else {
			routing.config.groups[group] = current.filter(
				(candidate) =>
					!(
						candidate.connection_id === item.connection_id &&
						candidate.model_id === item.model_id
					)
			);
		}
	}

	function connectionsForGroup(group: string) {
		if (!routing) return [];
		const allowed: (TierScope | undefined)[] = group.includes('-free-')
			? ['free', 'both', undefined]
			: ['plus', 'both', undefined];
		return routing.connections.filter((connection) => allowed.includes(connection.tier_scope));
	}

	function addCandidate(group: string) {
		if (!routing) return;
		const connection = connectionsForGroup(group).find((item) => item.enabled);
		if (!connection) return;
		const candidate: PoolCandidate = {
			connection_id: connection.id,
			model_id: '',
			priority: 0,
			min_context: 8192,
			capabilities: ['text'],
			enabled: true
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
				invalid_free_model:
					'OpenRouter Free models require an approved :free model with verified zero pricing.',
				model_pool_changed:
					'An administrator updated the model groups. Reload to get the latest settings.'
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
					{$i18n.t('Free messages per user per day')}
					<input
						type="number"
						class={inputClass}
						min="1"
						required
						bind:value={routing.config.free_user_daily_limit}
					/>
				</label>
				<label class="flex flex-col text-xs">
					{$i18n.t('Shared Free requests per day')}
					<input
						type="number"
						class={inputClass}
						min="1"
						required
						bind:value={routing.config.free_account_daily_limit}
					/>
				</label>
				<label class="flex flex-col text-xs">
					{$i18n.t('Shared Free requests per minute')}
					<input
						type="number"
						class={inputClass}
						min="1"
						required
						bind:value={routing.config.free_account_minute_limit}
					/>
				</label>
			</fieldset>

			<fieldset
				disabled={saving}
				class="flex flex-col gap-2 rounded-lg border border-gray-200 p-2.5 dark:border-gray-800"
			>
				<legend class="px-1 text-xs font-medium">
					{$i18n.t('Free tier :free models checklist')}
					{#if freeCatalog.length > 0}
						<span class="ml-1 text-gray-500">({freeCatalog.length})</span>
					{/if}
				</legend>
				<div class="flex flex-wrap items-center justify-between gap-2 text-xs">
					<input
						type="search"
						class="{inputClass} min-w-52 flex-1"
						placeholder={$i18n.t('Filter :free models...')}
						bind:value={catalogQuery}
					/>
					<button
						type="button"
						class="{inputClass} text-xs disabled:opacity-50"
						disabled={loadingCatalog}
						on:click={() => loadCatalog(true)}
					>
						{loadingCatalog ? $i18n.t('Loading...') : $i18n.t('Refresh :free list')}
					</button>
				</div>

				{#if catalogError}
					<p class="text-xs text-red-600 dark:text-red-400">{catalogError}</p>
				{:else if loadingCatalog && freeCatalog.length === 0}
					<p class="py-2 text-xs text-gray-500">{$i18n.t('Loading :free models...')}</p>
				{:else if filteredFreeCatalog.length === 0}
					<p class="py-2 text-xs text-gray-500">
						{freeCatalog.length === 0
							? $i18n.t(
									'No :free models found. Configure an enabled Free-tier connection (e.g. OpenRouter) in Connections.'
								)
							: $i18n.t('No :free models match your search.')}
					</p>
				{:else}
					<div class="max-h-72 overflow-y-auto rounded-lg border border-gray-100 dark:border-gray-850">
						<table class="w-full text-left text-xs">
							<thead class="sticky top-0 bg-gray-50 text-gray-600 dark:bg-gray-900 dark:text-gray-300">
								<tr>
									<th class="px-2.5 py-1.5 font-medium">{$i18n.t('Model')}</th>
									{#each freeGroupColumns as col (col.id)}
										<th class="w-24 px-2 py-1.5 text-center font-medium">
											{$i18n.t(col.label)}
										</th>
									{/each}
								</tr>
							</thead>
							<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
								{#each filteredFreeCatalog as item (`${item.connection_id}:${item.model_id}`)}
									<tr class="hover:bg-gray-50/70 dark:hover:bg-gray-850/50">
										<td class="px-2.5 py-1.5">
											<div class="font-medium text-gray-900 dark:text-gray-100">{item.name}</div>
											<div class="flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
												<span class="font-mono">{item.model_id}</span>
												<span class="rounded bg-gray-100 px-1 py-0.5 dark:bg-gray-800">
													{formatContext(item.context_length)}
												</span>
												{#each item.capabilities.filter((c) => c !== 'text') as cap (cap)}
													<span class="rounded bg-blue-500/10 px-1 py-0.5 text-blue-600 dark:text-blue-400">
														{$i18n.t(capabilityLabels[cap])}
													</span>
												{/each}
											</div>
										</td>
										{#each freeGroupColumns as col (col.id)}
											<td class="px-2 py-1.5 text-center">
												<input
													type="checkbox"
													aria-label={`${item.model_id} ${col.label}`}
													checked={isAssigned(col.id, item)}
													on:change={(e) =>
														toggleAssigned(col.id, item, e.currentTarget.checked)}
												/>
											</td>
										{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</fieldset>

			{#each Object.keys(routing.config.groups) as group (group)}
				<fieldset
					disabled={saving}
					class="flex flex-col gap-2 rounded-lg border border-gray-200 p-2 dark:border-gray-800"
				>
					<legend class="px-1 text-xs font-medium">
						{$i18n.t(groupLabels[group] ?? group)}
					</legend>
					{#each routing.config.groups[group] as candidate, index}
						<div class="flex flex-wrap items-end gap-2 text-xs">
							<label class="flex min-w-36 flex-col">
								{$i18n.t('Connection')}
								<select class={inputClass} bind:value={candidate.connection_id} required>
									{#if !connectionsForGroup(group).some((connection) => connection.id === candidate.connection_id)}
										<option value={candidate.connection_id}
											>{$i18n.t('Unavailable connection')}</option
										>
									{/if}
									{#each connectionsForGroup(group) as connection (connection.id)}
										<option value={connection.id}
											>{connection.url} · {connection.id.slice(0, 8)}</option
										>
									{/each}
								</select>
							</label>
							<label class="flex flex-col">
								{$i18n.t('Model ID')}
								<input class={inputClass} bind:value={candidate.model_id} required />
							</label>
							<label class="flex w-20 flex-col">
								{$i18n.t('Priority')}
								<input
									type="number"
									class={inputClass}
									min="0"
									required
									bind:value={candidate.priority}
								/>
							</label>
							<label class="flex w-28 flex-col">
								{$i18n.t('Context tokens')}
								<input
									type="number"
									class={inputClass}
									min="1"
									required
									bind:value={candidate.min_context}
								/>
							</label>
							<label class="pb-1">
								<input type="checkbox" bind:checked={candidate.enabled} />
								{$i18n.t('Enabled')}
							</label>
							<button
								type="button"
								class="pb-1 text-red-600 dark:text-red-400"
								on:click={() => {
									routing.config.groups[group] = routing.config.groups[group].filter(
										(_, i) => i !== index
									);
								}}>{$i18n.t('Remove')}</button
							>
							<div class="flex w-full flex-wrap gap-2">
								{#each capabilities as capability (capability)}
									<label>
										<input
											type="checkbox"
											bind:group={candidate.capabilities}
											value={capability}
											disabled={capability === 'text'}
										/>
										{$i18n.t(capabilityLabels[capability])}
									</label>
								{/each}
							</div>
						</div>
					{/each}
					<button
						type="button"
						class="self-start text-xs underline disabled:opacity-50"
						disabled={!connectionsForGroup(group).some((item) => item.enabled)}
						on:click={() => addCandidate(group)}
					>
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
