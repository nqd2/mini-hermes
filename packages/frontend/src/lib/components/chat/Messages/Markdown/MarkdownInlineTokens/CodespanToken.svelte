<script lang="ts">
	import { copyToClipboard, unescapeHtml } from '$lib/utils';
	import { toast } from 'svelte-sonner';

	import { getContext } from 'svelte';

	import { settings } from '$lib/stores';
	import FileLinkToken from './FileLinkToken.svelte';
	import { parseFileLinkUrl } from './fileLinkParser';

	const i18n = getContext('i18n');

	export let token;
	export let done = true;

	$: rawText = unescapeHtml(token?.text ?? '');
	$: fileRef = parseFileLinkUrl(rawText);
</script>

{#if fileRef}
	<FileLinkToken
		fileId={fileRef.fileId}
		source={fileRef.source}
		revision={fileRef.revision}
	/>
{:else}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
	<code
		class="codespan cursor-pointer {!done && ($settings?.chatFadeStreamingText ?? true)
			? 'fade-in-token'
			: ''}"
		on:click={() => {
			copyToClipboard(rawText);
			toast.success($i18n.t('Copied to clipboard'));
		}}>{rawText}</code
	>
{/if}
