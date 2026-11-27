<script lang="ts">
	// Keep keys in nested template expressions visible to i18next-parser.
	// $i18n.t("licensed under");

	import { WEAI_NAME, settings, showChangelog } from '$lib/stores';
	import { getContext } from 'svelte';

	import BuildVersion from '$lib/components/common/BuildVersion.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const actionButtonClass =
		'inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08] dark:hover:text-white';

</script>

<div id="tab-about" class="flex flex-col h-full justify-between text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.about.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<UserSettingSection
			title={`${$WEAI_NAME} ${$i18n.t('settings.personal.about.sections.version.title')}`}
			first
		>
			<UserSettingRow description={$i18n.t('settings.personal.about.seeWhatSNew.description')}>
				<div slot="label" class="flex flex-wrap items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300">
					<BuildVersion />
				</div>

				<button
					type="button"
					class={actionButtonClass}
					on:click={() => {
						showChangelog.set(true);
					}}
				>
					{$i18n.t('settings.personal.about.seeWhatSNew.label')}
				</button>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('Credits')}>
			<div class="space-y-1.5 text-xs text-gray-500 dark:text-gray-400">
				<div>
					{$i18n.t('Built on open-source software including')}
					<a
						class="underline decoration-gray-300 underline-offset-4 hover:text-gray-900 dark:decoration-gray-600 dark:hover:text-white"
						href="https://github.com/open-webui/open-webui"
						target="_blank"
						rel="noopener noreferrer">Open WebUI</a
					>.
				</div>
				<div>
					{$i18n.t('Emoji graphics provided by')}
					<a
						class="underline decoration-gray-300 underline-offset-4 hover:text-gray-900 dark:decoration-gray-600 dark:hover:text-white"
						href="https://github.com/jdecked/twemoji"
						target="_blank"
						rel="noopener noreferrer">Twemoji</a
					>, {$i18n.t('licensed under')}
					<a
						class="underline decoration-gray-300 underline-offset-4 hover:text-gray-900 dark:decoration-gray-600 dark:hover:text-white"
						href="https://creativecommons.org/licenses/by/4.0/"
						target="_blank"
						rel="noopener noreferrer">CC-BY 4.0</a
					>.
				</div>
			</div>
		</UserSettingSection>
	</div>
</div>
