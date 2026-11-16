<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	onMount(() => {
		goto('/workspace/documents?view=collections', { replaceState: true });
	});
</script>
