<script lang="ts">
	import { user } from '$lib/stores';
	import {
		loadOpenRouterProviderIcons,
		openRouterProviderIcons,
		resolveModelIcon
	} from '$lib/utils/providerIcons';

	export let modelId: string | null | undefined = '';
	export let className = 'size-5 rounded-full';
	export let alt = '';

	$: if ($user?.role === 'admin' && typeof localStorage !== 'undefined' && localStorage.token) {
		void loadOpenRouterProviderIcons(localStorage.token);
	}

	$: resolved = resolveModelIcon(modelId, $user?.role, $openRouterProviderIcons);
	let activeSrc = '/favicon.png';
	let usedFallback = false;

	$: {
		activeSrc = resolved.src;
		usedFallback = false;
	}

	const handleError = () => {
		if (!usedFallback && activeSrc !== resolved.fallback) {
			usedFallback = true;
			activeSrc = resolved.fallback;
		} else if (activeSrc !== '/favicon.png') {
			activeSrc = '/favicon.png';
		}
	};
</script>

<img
	src={activeSrc}
	{alt}
	aria-hidden={alt === ''}
	draggable="false"
	class="object-contain shrink-0 {className} {resolved.invertDark && activeSrc !== '/favicon.png'
		? 'dark:invert'
		: ''}"
	on:error={handleError}
/>
