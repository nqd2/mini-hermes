<script lang="ts">
	import { getContext } from 'svelte';
	import { WEAI_BUILD_CHANNEL, WEAI_BUILD_HASH, WEAI_VERSION } from '$lib/constants';
	import Tooltip from './Tooltip.svelte';

	const i18n: any = getContext('i18n');
</script>

<Tooltip content={WEAI_BUILD_HASH}>
	<span>
		v{WEAI_VERSION}{#if WEAI_BUILD_CHANNEL === 'dev'}
			{' '}({WEAI_BUILD_HASH ? `dev · ${WEAI_BUILD_HASH.slice(0, 9)}` : $i18n.t('dev build')})
		{:else if WEAI_BUILD_CHANNEL !== 'main'}
			{' '}({WEAI_BUILD_HASH
				? $i18n.t('build · {{hash}}', { hash: WEAI_BUILD_HASH.slice(0, 9) })
				: $i18n.t('unknown build')})
		{/if}
	</span>
</Tooltip>
