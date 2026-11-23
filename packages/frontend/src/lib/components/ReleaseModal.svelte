<script lang="ts">
	import { getContext } from 'svelte';
	import type { Release } from '$lib/apis/releases/release';
	import Modal from './common/Modal.svelte';

	const i18n = getContext('i18n');
	export let show = false;
	export let release: Release | null;
	export let close: () => void;
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
	</div>
</Modal>
