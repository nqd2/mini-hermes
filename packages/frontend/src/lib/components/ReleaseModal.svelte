<script lang="ts">
	import { getContext } from 'svelte';
	import { getReleases, type Release } from '$lib/apis/releases/release';
	import Modal from './common/Modal.svelte';

	const i18n = getContext<any>('i18n');
	export let show = false;
	export let release: Release | null;
	export let close: () => void;

	let activeTab: 'current' | 'history' = 'current';
	let history: Release[] | null = null;
	let loadingHistory = false;
	let historyError = false;

	async function loadHistory(force = false) {
		if (loadingHistory || (history !== null && !force)) return;
		loadingHistory = true;
		historyError = false;
		try {
			const token = typeof localStorage !== 'undefined' ? (localStorage.token ?? '') : '';
			history = await getReleases(token);
		} catch {
			historyError = true;
		} finally {
			loadingHistory = false;
		}
	}

	function selectTab(tab: 'current' | 'history') {
		activeTab = tab;
		if (tab === 'history') {
			void loadHistory();
		}
	}

	$: if (show) {
		activeTab = 'current';
	}
</script>

<Modal {show} onRequestClose={close} ariaLabel={$i18n.t('WeAI release notes')}>
	<div class="flex max-h-[65vh] flex-col gap-3 overflow-auto p-4 text-gray-900 dark:text-gray-100">
		<div class="flex items-start justify-between gap-3">
			<h2 class="text-base font-medium text-gray-900 dark:text-white">{$i18n.t('WeAI release notes')}</h2>
			<button
				type="button"
				class="text-sm text-gray-600 underline transition hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
				on:click={close}
			>
				{$i18n.t('Close')}
			</button>
		</div>

		<div class="flex gap-1 border-b border-gray-200 dark:border-gray-800 pb-2" role="tablist">
			<button
				type="button"
				role="tab"
				aria-selected={activeTab === 'current'}
				class="rounded-lg px-3 py-1.5 text-xs font-medium transition {activeTab === 'current'
					? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-white'
					: 'text-gray-500 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/60 dark:hover:text-gray-100'}"
				on:click={() => selectTab('current')}
			>
				{$i18n.t("What's new")}
			</button>
			<button
				type="button"
				role="tab"
				aria-selected={activeTab === 'history'}
				class="rounded-lg px-3 py-1.5 text-xs font-medium transition {activeTab === 'history'
					? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-white'
					: 'text-gray-500 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/60 dark:hover:text-gray-100'}"
				on:click={() => selectTab('history')}
			>
				{$i18n.t('Full changelog')}
			</button>
		</div>

		{#if activeTab === 'current'}
			{#if release && release.released_at}
				<p class="text-sm text-gray-500 dark:text-gray-400">
					{release.version} · <time datetime={release.released_at}>{release.released_at}</time>
				</p>
				{#each Object.entries(release.sections) as [section, items] (section)}
					<section class="text-sm">
						<h3 class="font-medium text-gray-900 dark:text-gray-100">{section}</h3>
						<ul class="mt-1 list-disc space-y-1 pl-5 text-gray-700 dark:text-gray-200">
							{#each items as item}
								<li>{item}</li>
							{/each}
						</ul>
					</section>
				{/each}
			{:else if release}
				<p class="text-sm text-gray-700 dark:text-gray-200">{$i18n.t('No release notes are available for this development build.')}</p>
			{:else}
				<p class="text-sm text-gray-700 dark:text-gray-200">{$i18n.t('Could not load WeAI release notes')}</p>
			{/if}
		{:else}
			{#if loadingHistory}
				<p class="text-sm text-gray-500 dark:text-gray-400">{$i18n.t('Loading release history...')}</p>
			{:else if historyError}
				<div class="flex items-center justify-between gap-2 text-sm text-gray-700 dark:text-gray-200">
					<span>{$i18n.t('Could not load WeAI release history')}</span>
					<button
						type="button"
						class="text-xs underline"
						on:click={() => loadHistory(true)}
					>
						{$i18n.t('Retry')}
					</button>
				</div>
			{:else if history && history.length > 0}
				<div class="flex flex-col gap-4">
					{#each history as entry (entry.version)}
						<article class="flex flex-col gap-2 border-b border-gray-100 pb-3 last:border-b-0 last:pb-0 dark:border-gray-800">
							<div class="flex items-center gap-2 text-sm">
								<span class="font-semibold text-gray-900 dark:text-white">{entry.version}</span>
								{#if entry.released_at}
									<span class="text-gray-500 dark:text-gray-400">
										· <time datetime={entry.released_at}>{entry.released_at}</time>
									</span>
								{/if}
								{#if release?.version === entry.version}
									<span class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200">
										{$i18n.t('Current')}
									</span>
								{/if}
							</div>
							{#each Object.entries(entry.sections) as [section, items] (section)}
								<section class="text-sm">
									<h3 class="font-medium text-gray-900 dark:text-gray-100">{section}</h3>
									<ul class="mt-1 list-disc space-y-1 pl-5 text-gray-700 dark:text-gray-200">
										{#each items as item}
											<li>{item}</li>
										{/each}
									</ul>
								</section>
							{/each}
						</article>
					{/each}
				</div>
			{:else}
				<p class="text-sm text-gray-500 dark:text-gray-400">{$i18n.t('No release history available.')}</p>
			{/if}
		{/if}
	</div>
</Modal>
