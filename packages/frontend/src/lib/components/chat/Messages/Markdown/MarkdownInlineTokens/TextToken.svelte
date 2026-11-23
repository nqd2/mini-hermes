<script lang="ts">
	import { settings } from '$lib/stores';
	import FileLinkToken from './FileLinkToken.svelte';
	import { splitTextWithFileLinks } from './fileLinkParser';

	export let token;
	export let done = true;

	$: raw = token?.raw ?? '';
	$: segments = splitTextWithFileLinks(raw);
	$: hasFileLink = segments.length > 1 || segments[0]?.type === 'file';
</script>

{#if hasFileLink}
	{#each segments as segment}
		{#if segment.type === 'file'}
			<FileLinkToken
				fileId={segment.ref.fileId}
				source={segment.ref.source}
				revision={segment.ref.revision}
			/>
		{:else}
			{segment.text}
		{/if}
	{/each}
{:else if done || !($settings?.chatFadeStreamingText ?? true)}
	{raw}
{:else}
	{#each raw.split(' ') as text}
		<span class="fade-in-token">
			{text}{' '}
		</span>
	{/each}
{/if}
