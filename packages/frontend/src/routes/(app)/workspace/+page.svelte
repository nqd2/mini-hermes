<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/workspace/documents', { replaceState: true });
	});
</script>
