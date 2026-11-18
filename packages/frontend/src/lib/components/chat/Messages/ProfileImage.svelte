<script lang="ts">
	import { WEAI_BASE_URL } from '$lib/constants';
	import { safeImageUrl } from '$lib/utils/safeImageUrl';

	export let className = 'size-8';
	const FALLBACK_SRC = `${WEAI_BASE_URL}/static/favicon.png`;

	export let src = FALLBACK_SRC;
	export let allowExternal = false;
</script>

<img
	aria-hidden="true"
	src={safeImageUrl(src, allowExternal)}
	class=" {className} object-cover rounded-2xl"
	alt="profile"
	draggable="false"
	on:error={(e) => {
		if (!e.currentTarget.src.endsWith(FALLBACK_SRC)) {
			e.currentTarget.src = FALLBACK_SRC;
		}
	}}
/>
