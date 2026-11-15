<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import ConsecutiveDetailsGroup from '../Markdown/ConsecutiveDetailsGroup.svelte';
	import CodeBlock from '../CodeBlock.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';
	import type { TraceStep } from './documentAnalysisTrace';

	export let steps: TraceStep[] = [];
	export let phaseLabels: Record<string, string>;
	export let active = false;
	export let failed = false;
	export let hasMore = false;
	export let loadMore: () => void;

	const i18n = getContext<Writable<I18n>>('i18n');
	const statuses = {
		running: 'Running', succeeded: 'Succeeded', failed: 'Failed',
		cancelled: 'Cancelled', interrupted: 'Interrupted'
	};
	let visible = 30;
</script>

{#if steps.length}
	<ConsecutiveDetailsGroup heading={$i18n.t('Analysis activity')} workerState={active ? 'running' : failed ? 'failed' : 'complete'}>
		<div slot="content" class="space-y-0.5">
			{#if steps.length > visible}
				<button type="button" class="text-xs underline" on:click={() => (visible += 30)}>
					{$i18n.t('Show earlier analysis steps')}
				</button>
			{/if}
			{#each steps.slice(-visible) as step (step.id)}
				{@const state = step.status === 'running' && !active && !hasMore ? 'interrupted' : step.status}
				{@const scope = step.scope}
				{@const sourceRange = scope?.first_evidence
					? scope.last_evidence === scope.first_evidence
						? `E${scope.first_evidence}`
						: `E${scope.first_evidence}–E${scope.last_evidence}`
					: ''}
				<ToolCallDisplay
					id={`analysis-step-${step.id}`}
					attributes={{ name: [$i18n.t(phaseLabels[step.phase] ?? 'Analyzing document'), scope?.format?.toUpperCase(), sourceRange, $i18n.t(statuses[state])].filter(Boolean).join(' · '), done: String(state !== 'running') }}
					workerStatus={state}
					grouped={true}
				>
					<div slot="details" class="space-y-2">
						{#if scope?.notes}
							<p class="text-xs">{$i18n.t('Evidence notes')} · {scope.notes}</p>
						{/if}
						{#if step.code}
							<p class="text-xs">{$i18n.t('Code')} · {step.function} · {step.source_version}</p>
							<CodeBlock token={null} code={step.code} lang="python" run={false} edit={false} />
							{#if step.code_truncated}<p class="text-xs">{$i18n.t('Source excerpt')}</p>{/if}
						{/if}
						{#if step.stdout}
							<div>
								<p class="text-xs">stdout</p>
								<pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded p-2 text-xs dark:bg-gray-900">{step.stdout}</pre>
							</div>
						{/if}
						{#if step.stderr}
							<div>
								<p class="text-xs">stderr</p>
								<pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded p-2 text-xs text-red-400 dark:bg-gray-900">{step.stderr}</pre>
							</div>
						{/if}
						{#if step.truncated.length}
							<p class="text-xs">{$i18n.t('Output truncated')} · {step.truncated.join(', ')}</p>
						{/if}
						{#if typeof step.elapsed_ms === 'number'}
							<p class="text-xs">{(step.elapsed_ms / 1000).toFixed(1)} s</p>
						{/if}
					</div>
				</ToolCallDisplay>
			{/each}
			{#if !active && hasMore}
				<button type="button" class="text-xs underline" on:click={loadMore}>
					{$i18n.t('Load more analysis steps')}
				</button>
			{/if}
		</div>
	</ConsecutiveDetailsGroup>
{/if}
