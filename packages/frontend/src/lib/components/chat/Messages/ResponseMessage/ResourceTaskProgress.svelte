<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export type ResourceTaskProgressData = {
		schema_version?: number;
		kind: 'summary' | 'index';
		status: string;
		task_id?: string | null;
		index_job_id?: string | null;
		filename?: string | null;
		phase?: string;
		progress?: { completed_units: number; total_units: number };
		error?: string | null;
	};

	export let receipt: ResourceTaskProgressData;
	export let chatId: string = '';
	export let messageId: string = '';

	const i18n = getContext<Writable<I18n>>('i18n');
	const controller = new AbortController();
	let timer: ReturnType<typeof setTimeout> | undefined;
	let busy = false;
	let localError = '';
	let current: ResourceTaskProgressData = receipt;

	$: if (receipt) {
		current = receipt;
	}

	$: completed = current?.progress?.completed_units ?? 0;
	$: total = current?.progress?.total_units ?? 0;
	$: percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
	$: active = current?.status === 'pending' || current?.status === 'processing';
	$: resumable = current?.status === 'failed' || current?.status === 'cancelled';

	const phaseLabels: Record<string, string> = {
		indexing: 'Indexing document',
		chunks: 'Reading document sections',
		reduce: 'Combining findings'
	};

	async function pollOnce() {
		if (controller.signal.aborted || !active) return;
		try {
			const url =
				current.kind === 'index' && current.index_job_id
					? `${WEBUI_BASE_URL}/api/v1/mini-hermes/index-jobs/${encodeURIComponent(current.index_job_id)}`
					: current.task_id
						? `${WEBUI_BASE_URL}/api/v1/mini-hermes/tasks/${encodeURIComponent(current.task_id)}?include_analysis=false&evidence_limit=1`
						: null;
			if (!url) return;
			const res = await fetch(url, {
				headers: { Authorization: `Bearer ${localStorage.token}` },
				signal: controller.signal
			});
			if (res.ok) {
				const data = await res.json();
				current = {
					...current,
					status: data.status ?? current.status,
					phase: data.phase ?? current.phase,
					filename: data.filename ?? current.filename,
					progress: data.progress ?? current.progress,
					error: data.error ?? current.error
				};
			}
		} catch {
			// Socket updates remain primary; ignore transient poll errors.
		} finally {
			if (!controller.signal.aborted && (current.status === 'pending' || current.status === 'processing')) {
				timer = setTimeout(pollOnce, 3000);
			}
		}
	}

	async function cancelTask() {
		if (!current.task_id || busy) return;
		busy = true;
		localError = '';
		try {
			const res = await fetch(
				`${WEBUI_BASE_URL}/api/v1/mini-hermes/tasks/${encodeURIComponent(current.task_id)}/cancel`,
				{ method: 'POST', headers: { Authorization: `Bearer ${localStorage.token}` }, signal: controller.signal }
			);
			if (!res.ok) throw new Error(String(res.status));
			current = { ...current, status: 'cancelled' };
		} catch {
			localError = 'Could not cancel task.';
		} finally {
			busy = false;
		}
	}

	async function retryTask() {
		if (!chatId || !messageId || busy) return;
		busy = true;
		localError = '';
		try {
			const res = await fetch(
				`${WEBUI_BASE_URL}/api/v1/mini-hermes/chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/resume-resource`,
				{ method: 'POST', headers: { Authorization: `Bearer ${localStorage.token}` }, signal: controller.signal }
			);
			if (!res.ok) throw new Error(String(res.status));
			const payload = await res.json();
			if (payload.resource_task_progress) {
				current = payload.resource_task_progress;
			}
			clearTimeout(timer);
			timer = setTimeout(pollOnce, 2000);
		} catch {
			localError = 'Could not resume task.';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		if (active) {
			timer = setTimeout(pollOnce, 3000);
		}
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	});
</script>

<div
	class="my-2 rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-900/50 px-3.5 py-2.5 text-sm"
>
	<div class="flex items-center justify-between gap-3">
		<div class="flex items-center gap-2 min-w-0">
			{#if active}
				<Spinner className="size-4 shrink-0" />
			{/if}
			<div class="min-w-0">
				<div class="font-medium text-gray-900 dark:text-gray-100 truncate">
					{$i18n.t(
						phaseLabels[current.phase ?? ''] ??
							(current.kind === 'index' ? 'Indexing document' : 'Processing document')
					)}
					{#if current.filename}
						<span class="font-normal text-gray-500 dark:text-gray-400">· {current.filename}</span>
					{/if}
				</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{#if current.status === 'completed'}
						{$i18n.t('Finalizing response...')}
					{:else if total > 0}
						{completed}/{total} ({percent}%)
					{:else}
						{$i18n.t(current.status)}
					{/if}
				</div>
			</div>
		</div>

		<div class="flex items-center gap-2 shrink-0">
			{#if active && current.task_id}
				<button
					type="button"
					disabled={busy}
					on:click={cancelTask}
					class="px-2.5 py-1 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-200/70 dark:hover:bg-gray-800 transition disabled:opacity-50"
				>
					{$i18n.t('Cancel')}
				</button>
			{:else if resumable && chatId && messageId}
				<button
					type="button"
					disabled={busy}
					on:click={retryTask}
					class="px-2.5 py-1 rounded-lg text-xs font-medium bg-gray-900 text-white dark:bg-white dark:text-gray-900 hover:opacity-90 transition disabled:opacity-50"
				>
					{$i18n.t('Retry')}
				</button>
			{/if}
		</div>
	</div>

	{#if active || current.status === 'completed'}
		<div class="mt-2 h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
			<div
				class="h-full bg-gray-900 dark:bg-gray-100 transition-all duration-300"
				style="width: {current.status === 'completed' ? 100 : total > 0 ? percent : 12}%"
			/>
		</div>
	{/if}

	{#if localError || current.error}
		<div class="mt-1.5 text-xs text-red-600 dark:text-red-400">
			{localError || current.error}
		</div>
	{/if}
</div>
