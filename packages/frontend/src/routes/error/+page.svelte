<script>
	import { goto } from '$app/navigation';
	import { WEAI_BASE_URL } from '$lib/constants';
	import { WEAI_NAME, config } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	$: backendUrl = WEAI_BASE_URL || (typeof window !== 'undefined' ? window.location.origin : '');

	onMount(async () => {
		if ($config) {
			await goto('/');
		}

		loaded = true;
	});
</script>

{#if loaded}
	<div class="absolute w-full h-full flex z-50">
		<div class="absolute rounded-xl w-full h-full backdrop-blur-sm flex justify-center">
			<div class="m-auto pb-44 flex flex-col justify-center">
				<div class="max-w-md px-4">
					<div class="text-center text-2xl font-normal z-50">
						{$i18n.t('{{webUIName}} Backend Required', { webUIName: $WEAI_NAME })}
					</div>

					<div class="mt-4 text-center text-sm w-full">
						{$i18n.t(
							'Cannot connect to the backend. Check its URL and make sure the backend is running.'
						)}
						{#if backendUrl}
							<div class="mt-3 rounded-lg bg-gray-100 px-3 py-1.5 font-mono text-xs text-gray-700 dark:bg-gray-850 dark:text-gray-300">
								{backendUrl}
							</div>
						{/if}
					</div>

					<div class="mt-6 mx-auto relative group w-fit">
						<button
							class="relative z-20 flex px-5 py-2 rounded-full bg-gray-100 hover:bg-gray-200 transition font-normal text-sm text-black"
							on:click={() => {
								location.href = '/';
							}}
						>
							{$i18n.t('Check Again')}
						</button>
					</div>
				</div>
			</div>
		</div>
	</div>
{/if}
