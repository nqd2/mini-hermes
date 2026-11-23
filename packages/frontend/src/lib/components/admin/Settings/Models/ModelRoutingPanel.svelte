<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ModelSettingsLabel from '$lib/components/workspace/Models/ModelSettingsLabel.svelte';
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
	const inputClass =
		'w-full appearance-none rounded-xl border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-900 outline-hidden transition focus:border-gray-400 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-gray-600';
	const selectClass = `${inputClass} pr-7`;
	const optionClass = 'bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100';

	let routing: RoutingSettings | null = null;
	let freeCatalog: FreeCatalogModel[] = [];
	let loadingCatalog = false;
	let catalogError = '';
	let catalogQuery = '';
	let error = '';
	let saving = false;
	let isOpen = false;

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
					capabilities: Array.from(
						new Set<Capability>(['text', 'responses', ...item.capabilities])
					),
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
			capabilities: ['text', 'responses'],
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

<details class="group shrink-0 -mx-1" bind:open={isOpen}>
	<summary
		class="flex w-full cursor-pointer list-none items-center justify-between gap-2 rounded-md px-1 py-1.5 text-left text-xs font-normal text-gray-600 select-none focus-visible:outline focus-visible:outline-2 dark:text-gray-400 [&::-webkit-details-marker]:hidden"
	>
		<ModelSettingsLabel
			label={$i18n.t('Tier model routing')}
			description={$i18n.t(
				'Configure daily Free-tier quotas, OpenRouter :free model checklist, and fallback pools for Free and Plus model tiers.'
			)}
		/>
		<ChevronRight
			className="size-3 text-gray-400 transition-transform group-open:rotate-90"
		/>
	</summary>

	{#if isOpen}
	<div class="mt-2 rounded-2xl border border-gray-100 bg-gray-50/40 p-3.5 text-sm dark:border-gray-850 dark:bg-gray-900/40">
		{#if error}
			<p
				class="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400"
				role="alert"
			>
				{error}
			</p>
		{/if}
		{#if routing}
			<form class="flex flex-col gap-3.5" on:submit|preventDefault={save}>
				<fieldset disabled={saving} class="grid grid-cols-1 gap-3 sm:grid-cols-3">
					<label class="flex flex-col gap-1 text-xs font-medium text-gray-600 dark:text-gray-400">
						<span>{$i18n.t('Free messages per user per day')}</span>
						<input
							type="number"
							class={inputClass}
							min="1"
							required
							bind:value={routing.config.free_user_daily_limit}
						/>
					</label>
					<label class="flex flex-col gap-1 text-xs font-medium text-gray-600 dark:text-gray-400">
						<span>{$i18n.t('Shared Free requests per day')}</span>
						<input
							type="number"
							class={inputClass}
							min="1"
							required
							bind:value={routing.config.free_account_daily_limit}
						/>
					</label>
					<label class="flex flex-col gap-1 text-xs font-medium text-gray-600 dark:text-gray-400">
						<span>{$i18n.t('Shared Free requests per minute')}</span>
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
					class="flex flex-col gap-2.5 rounded-xl border border-gray-200/80 bg-white p-3 dark:border-gray-800 dark:bg-gray-900/70"
				>
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex items-center gap-1.5 text-xs font-medium text-gray-800 dark:text-gray-200">
							<span>{$i18n.t('Free tier :free models checklist')}</span>
							{#if freeCatalog.length > 0}
								<span
									class="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400"
								>
									{freeCatalog.length}
								</span>
							{/if}
						</div>

						<div class="flex flex-wrap items-center gap-2 text-xs">
							<input
								type="search"
								class="{inputClass} min-w-48 sm:w-60"
								placeholder={$i18n.t('Filter :free models...')}
								bind:value={catalogQuery}
							/>
							<button
								type="button"
								class="shrink-0 rounded-xl border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 disabled:opacity-50 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-200 dark:hover:bg-gray-800"
								disabled={loadingCatalog}
								on:click={() => loadCatalog(true)}
							>
								{loadingCatalog ? $i18n.t('Loading...') : $i18n.t('Refresh :free list')}
							</button>
						</div>
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
						<div class="max-h-64 overflow-y-auto rounded-xl border border-gray-100 scrollbar-hover dark:border-gray-850">
							<table class="w-full text-left text-xs">
								<thead class="sticky top-0 z-10 bg-gray-50/95 text-gray-600 backdrop-blur-xs dark:bg-gray-850/95 dark:text-gray-300">
									<tr>
										<th class="px-3 py-2 font-medium">{$i18n.t('Model')}</th>
										{#each freeGroupColumns as col (col.id)}
											<th class="w-24 px-2 py-2 text-center font-medium">
												{$i18n.t(col.label)}
											</th>
										{/each}
									</tr>
								</thead>
								<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
									{#each filteredFreeCatalog as item (`${item.connection_id}:${item.model_id}`)}
										<tr class="transition hover:bg-gray-50/80 dark:hover:bg-gray-850/50">
											<td class="px-3 py-2">
												<div class="font-medium text-gray-900 dark:text-gray-100">{item.name}</div>
												<div class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
													<span class="font-mono">{item.model_id}</span>
													<span class="rounded-md bg-gray-100 px-1.5 py-0.5 dark:bg-gray-800">
														{formatContext(item.context_length)}
													</span>
													{#each item.capabilities.filter((c) => c !== 'text') as cap (cap)}
														<span class="rounded-md bg-blue-500/10 px-1.5 py-0.5 text-blue-600 dark:text-blue-400">
															{$i18n.t(capabilityLabels[cap])}
														</span>
													{/each}
												</div>
											</td>
											{#each freeGroupColumns as col (col.id)}
												<td class="px-2 py-2 text-center">
													<input
														type="checkbox"
														class="size-4 cursor-pointer rounded border-gray-300 accent-gray-900 dark:border-gray-700 dark:accent-white"
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

				<div class="grid grid-cols-1 gap-3">
					{#each Object.keys(routing.config.groups) as group (group)}
						<fieldset
							disabled={saving}
							class="flex flex-col gap-2.5 rounded-xl border border-gray-200/80 bg-white p-3 dark:border-gray-800 dark:bg-gray-900/70"
						>
							<div class="flex items-center justify-between gap-2">
								<div class="flex items-center gap-1.5 text-xs font-medium text-gray-800 dark:text-gray-200">
									<span>{$i18n.t(groupLabels[group] ?? group)}</span>
									<span
										class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-normal text-gray-600 dark:bg-gray-800 dark:text-gray-400"
									>
										{routing.config.groups[group].length}
									</span>
								</div>
								<button
									type="button"
									class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-100 disabled:opacity-50 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-200 dark:hover:bg-gray-800"
									disabled={!connectionsForGroup(group).some((item) => item.enabled)}
									on:click={() => addCandidate(group)}
								>
									+ {$i18n.t('Add model')}
								</button>
							</div>

							{#if routing.config.groups[group].length === 0}
								<p class="text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('No models assigned to this tier group yet.')}
								</p>
							{/if}

							{#each routing.config.groups[group] as candidate, index}
								<div class="flex flex-col gap-2 rounded-xl border border-gray-100 bg-gray-50/60 p-2.5 text-xs dark:border-gray-850 dark:bg-gray-850/40">
									<div class="flex flex-wrap items-end gap-2">
										<label class="flex min-w-40 flex-1 flex-col gap-1 text-gray-600 dark:text-gray-400">
											<span>{$i18n.t('Connection')}</span>
											<select class={selectClass} bind:value={candidate.connection_id} required>
												{#if !connectionsForGroup(group).some((connection) => connection.id === candidate.connection_id)}
													<option class={optionClass} value={candidate.connection_id}
														>{$i18n.t('Unavailable connection')}</option
													>
												{/if}
												{#each connectionsForGroup(group) as connection (connection.id)}
													<option class={optionClass} value={connection.id}
														>{connection.url} · {connection.id.slice(0, 8)}</option
													>
												{/each}
											</select>
										</label>
										<label class="flex min-w-44 flex-[1.5] flex-col gap-1 text-gray-600 dark:text-gray-400">
											<span>{$i18n.t('Model ID')}</span>
											<input class={inputClass} bind:value={candidate.model_id} required />
										</label>
										<label class="flex w-20 flex-col gap-1 text-gray-600 dark:text-gray-400">
											<span>{$i18n.t('Priority')}</span>
											<input
												type="number"
												class={inputClass}
												min="0"
												required
												bind:value={candidate.priority}
											/>
										</label>
										<label class="flex w-28 flex-col gap-1 text-gray-600 dark:text-gray-400">
											<span>{$i18n.t('Context tokens')}</span>
											<input
												type="number"
												class={inputClass}
												min="1"
												required
												bind:value={candidate.min_context}
											/>
										</label>
										<div class="flex items-center gap-2 pb-1">
											<label class="inline-flex cursor-pointer items-center gap-1.5 text-gray-700 dark:text-gray-300">
												<input
													type="checkbox"
													class="size-3.5 rounded border-gray-300 accent-gray-900 dark:border-gray-700 dark:accent-white"
													bind:checked={candidate.enabled}
												/>
												<span>{$i18n.t('Enabled')}</span>
											</label>
											<button
												type="button"
												class="rounded-lg px-2 py-1 text-xs font-medium text-red-600 transition hover:bg-red-500/10 dark:text-red-400"
												on:click={() => {
													routing.config.groups[group] = routing.config.groups[group].filter(
														(_, i) => i !== index
													);
												}}>{$i18n.t('Remove')}</button
											>
										</div>
									</div>

									<div class="flex flex-wrap items-center gap-3 pt-0.5 text-[11px] text-gray-600 dark:text-gray-400">
										{#each capabilities as capability (capability)}
											<label class="inline-flex cursor-pointer items-center gap-1">
												<input
													type="checkbox"
													class="size-3.5 rounded border-gray-300 accent-gray-900 dark:border-gray-700 dark:accent-white"
													bind:group={candidate.capabilities}
													value={capability}
													disabled={capability === 'text'}
												/>
												<span>{$i18n.t(capabilityLabels[capability])}</span>
											</label>
										{/each}
									</div>
								</div>
							{/each}
						</fieldset>
					{/each}
				</div>

				<div class="flex items-center justify-end gap-3 pt-1">
					{#if error}
						<button
							type="button"
							class="rounded-lg px-2.5 py-1.5 text-xs font-medium text-gray-600 underline hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
							on:click={load}
						>
							{$i18n.t('Reload')}
						</button>
					{/if}
					<button
						type="submit"
						class="flex h-8 items-center justify-center gap-1.5 rounded-xl bg-gray-900 px-3.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
						disabled={saving}
					>
						{$i18n.t('Save routing')}
					</button>
				</div>
			</form>
		{:else if error}
			<button
				type="button"
				class="mt-1 rounded-lg px-2.5 py-1 text-xs font-medium underline"
				on:click={load}
			>
				{$i18n.t('Retry')}
			</button>
		{/if}
	</div>
	{/if}
</details>
