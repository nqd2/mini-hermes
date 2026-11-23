<script lang="ts">
	import { getMaterialIconPath } from './materialFileIcon';

	export let filename: string | null | undefined = '';
	export let className = 'size-6';

	$: src = getMaterialIconPath(filename);
</script>

<img
	{src}
	alt=""
	aria-hidden="true"
	draggable="false"
	class="inline-block select-none object-contain {className}"
/>
