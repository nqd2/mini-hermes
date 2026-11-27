<script lang="ts">
	import { getContext } from 'svelte';
	import {
		WEAI_BUILD_CHANNEL,
		WEAI_BUILD_HASH,
		WEAI_VERSION as STATIC_VERSION
	} from '$lib/constants';
	import { WEAI_VERSION as runtimeVersion } from '$lib/stores';
	import Tooltip from './Tooltip.svelte';

	const i18n: any = getContext('i18n');
	$: versionLabel = STATIC_VERSION || $runtimeVersion || '0.1.3';
</script>

<Tooltip content={WEAI_BUILD_HASH}>
	<span>
		v{versionLabel}{#if WEAI_BUILD_CHANNEL === 'dev'}
			{' '}({WEAI_BUILD_HASH ? `dev · ${WEAI_BUILD_HASH.slice(0, 9)}` : $i18n.t('dev build')})
		{:else if WEAI_BUILD_CHANNEL !== 'main'}
			{' '}({WEAI_BUILD_HASH
				? $i18n.t('build · {{hash}}', { hash: WEAI_BUILD_HASH.slice(0, 9) })
				: $i18n.t('unknown build')})
		{/if}
	</span>
</Tooltip>
