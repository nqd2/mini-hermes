<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import DocumentAnalysisTrace from './DocumentAnalysisTrace.svelte';
	import { mergeTrace, type TraceState } from './documentAnalysisTrace';
	import {
		analysisActive,
		changeAnalysis,
		readAnalysis,
		type AnalysisPage,
		type AnalysisReceipt,
		type AnalysisStatus
	} from './documentAnalysis';

	export let receipt: AnalysisReceipt;
	export let onResult: (task: AnalysisStatus) => void;

	const i18n = getContext<Writable<I18n>>('i18n');
	const controller = new AbortController();
	let timer: ReturnType<typeof setTimeout> | undefined;
	let status = receipt.status;
	let phase = receipt.phase;
	let progress = receipt.progress;
	let failure = '';
	let busy = false;
	let evidence: AnalysisPage | undefined;
	let trace: TraceState = { after: 0, steps: [] };
	let moreTrace = false;
	let polling = false;
	let actionVersion = 0;

	const labels: Record<string, string> = {
		survey: 'Surveying document',
		ocr: 'Reading images with OCR',
		summarize: 'Reading document evidence',
		reduce: 'Combining findings'
	};
	const errors: Record<string, string> = {
		no_readable_content: 'No readable content was found.',
		resource_unavailable_or_revised: 'The file changed or is no longer accessible.',
		revision_changed: 'The file changed. Start a new summary.',
		analysis_step_failed: 'Document analysis failed. You can retry.'
	};

	function schedule(delay = 2000) {
		clearTimeout(timer);
		if (!controller.signal.aborted) timer = setTimeout(poll, delay);
	}

	async function poll() {
		if (polling || controller.signal.aborted) return;
		polling = true;
		const version = actionVersion;
		try {
			const task = await readAnalysis(receipt.task_id, controller.signal, undefined, trace.after);
			if (controller.signal.aborted || version !== actionVersion) return;
			const wasActive = analysisActive(status);
			status = task.status;
			phase = task.phase;
			progress = task.progress;
			failure = task.error ? errors[task.error] ?? '' : '';
			if (task.trace) {
				trace = mergeTrace(trace, task.trace.events);
				moreTrace = task.trace.has_more;
			}
			if (analysisActive(status)) schedule();
			else if (wasActive) onResult(task);
		} catch (error) {
			if (controller.signal.aborted || version !== actionVersion) return;
			if (error instanceof Error && ['401', '403', '404', '409'].includes(error.message)) {
				status = 'unavailable';
				failure = 'The file changed or is no longer accessible.';
				trace = { after: 0, steps: [] };
				moreTrace = false;
				evidence = undefined;
			} else {
				schedule(5000);
			}
		} finally {
			polling = false;
			if (version !== actionVersion) schedule(0);
		}
	}

	async function change(action: 'cancel' | 'resume') {
		busy = true;
		try {
			await changeAnalysis(receipt.task_id, action, controller.signal);
			actionVersion += 1;
			if (action === 'resume') {
				status = 'pending';
				failure = '';
			}
			schedule(0);
		} catch {
			if (!controller.signal.aborted) failure = 'Could not update document analysis.';
		} finally {
			busy = false;
		}
	}

	async function nextEvidence() {
		busy = true;
		try {
			const task = await readAnalysis(receipt.task_id, controller.signal, evidence?.next_offset ?? 0);
			if (!controller.signal.aborted) evidence = task.analysis;
		} catch {
			if (!controller.signal.aborted) failure = 'Could not load document evidence.';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		schedule(0);
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	});
</script>

<div class="my-2 flex flex-wrap items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
	{#if analysisActive(status)}
		<span role="status">
			{$i18n.t(labels[phase] ?? 'Analyzing document')}
			{#if progress.total_units > 0}
				· {progress.completed_units}/{progress.total_units}
			{/if}
		</span>
		<button type="button" class="underline" disabled={busy} on:click={() => change('cancel')}>{$i18n.t('Cancel')}</button>
	{:else if status === 'failed' || status === 'cancelled'}
		<span role="status">{$i18n.t(failure || 'Document analysis cancelled.')}</span>
		<button type="button" class="underline" disabled={busy} on:click={() => change('resume')}>{$i18n.t('Retry')}</button>
	{:else if status === 'completed' || status === 'partial'}
		<button type="button" class="underline" disabled={busy} on:click={() => evidence ? (evidence = undefined) : nextEvidence()}>
			{$i18n.t(evidence ? 'Hide document evidence' : 'View document evidence')}
		</button>
	{:else if failure}
		<span role="status">{$i18n.t(failure)}</span>
	{/if}
</div>

<DocumentAnalysisTrace
	steps={trace.steps}
	phaseLabels={labels}
	active={analysisActive(status)}
	failed={status === 'failed' || status === 'cancelled'}
	hasMore={moreTrace}
	loadMore={poll}
/>

{#if evidence}
	<div class="my-2 rounded-lg border p-3 dark:border-gray-700">
		{#each evidence.gaps as gap}
			<p class="text-xs">{gap.locator} · {$i18n.t('Unread or incomplete')}</p>
		{/each}
		{#each evidence.evidence as item (item.id)}
			<details class="py-1 text-sm">
				<summary class="cursor-pointer">{item.id} · {item.locator}{item.kind === 'ocr' ? ' · OCR' : ''}</summary>
				{#if item.confidence?.length}
					<p class="text-xs">{$i18n.t('Lowest OCR confidence')}: {Math.round(Math.min(...item.confidence) * 100)}%</p>
				{/if}
				<p class="whitespace-pre-wrap break-words py-1">{item.text}</p>
			</details>
		{/each}
		{#if evidence.next_offset !== null}
			<button type="button" class="text-sm underline" disabled={busy} on:click={nextEvidence}>{$i18n.t('Next')}</button>
		{/if}
	</div>
{/if}
