<script lang="ts">
	import { getContext } from 'svelte';
	import type { MhRelease } from '$lib/apis/mini-hermes/release';
	import Modal from './common/Modal.svelte';

	const i18n = getContext('i18n');
	export let show = false;
	export let release: MhRelease | null;
	export let close: () => void;
</script>

<Modal {show} onRequestClose={close} ariaLabel={$i18n.t('Mini-Hermes release notes')}>
	<div class="flex max-h-[65vh] flex-col gap-3 overflow-auto p-4">
		<div class="flex items-start justify-between gap-3">
			<h2 class="text-base font-medium">{$i18n.t('Mini-Hermes release notes')}</h2>
			<button type="button" class="text-sm underline" on:click={close}>{$i18n.t('Close')}</button>
		</div>
		{#if release && release.released_at}
			<p class="text-sm text-gray-500">
				{release.version} · <time datetime={release.released_at}>{release.released_at}</time>
			</p>
			{#each Object.entries(release.sections) as [section, items] (section)}
				<section class="text-sm">
					<h3 class="font-medium">{section}</h3>
					<ul class="list-disc pl-5">
						{#each items as item}
							<li>{item}</li>
						{/each}
					</ul>
				</section>
			{/each}
		{:else if release}
			<p class="text-sm">{$i18n.t('No release notes are available for this development build.')}</p>
		{:else}
			<p class="text-sm">{$i18n.t('Could not load Mini-Hermes release notes')}</p>
		{/if}
	</div>
</Modal>
