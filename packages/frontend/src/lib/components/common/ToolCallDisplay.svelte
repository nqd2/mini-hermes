<script lang="ts">
	import { decode } from 'html-entities';
	import { v4 as uuidv4 } from 'uuid';

	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	const i18n = getContext<Writable<i18nType>>('i18n');

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import Spinner from './Spinner.svelte';
	import WrenchSolid from '../icons/WrenchSolid.svelte';
	import CheckCircle from '../icons/CheckCircle.svelte';
	import XMark from '../icons/XMark.svelte';
	import Image from './Image.svelte';
	import FullHeightIframe from './FullHeightIframe.svelte';
	import MCPApp from '$lib/components/chat/Messages/MCPApp.svelte';
	import { settings } from '$lib/stores';
	import { WEAI_BASE_URL } from '$lib/constants';
	import { formatToolDisplayName } from '$lib/utils/toolName';

	export let id: string = '';
	export let attributes: {
		type?: string;
		id?: string;
		name?: string;
		arguments?: string;
		result?: string;
		files?: string;
		embeds?: string;
		done?: string;
		status?: string;
	} = {};

	export let open = false;
	export let grouped = false;
	export let allowEmbeds = false;
	export let className = '';
	export let resolvable = false;
	export let resolving = false;
	export let onResolve: (approved: boolean) => void = () => {};

	const RESULT_PREVIEW_LIMIT = 10000;
	let expandedResult = false;
	$: displayName = formatToolDisplayName(attributes.name);

	$: if (!open) expandedResult = false;
	export let buttonClassName =
		'py-1 text-[0.9375rem] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition';

	const componentId = id || uuidv4();

	function parseJSONString(str: string) {
		// Iteratively unwrap nested JSON-encoded strings. Same result as the previous
		// recursive form, but without the stack-overflow-and-recover path it hit on
		// scalar values (e.g. JSON.parse('5') -> 5 -> infinite self-recursion).
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		let value: any = str;
		while (typeof value === 'string') {
			try {
				value = JSON.parse(value);
			} catch {
				break;
			}
		}
		return value;
	}

	function formatJSONString(str: string) {
		try {
			const parsed = parseJSONString(str);
			if (typeof parsed === 'object') {
				return JSON.stringify(parsed, null, 2);
			} else {
				return String(parsed);
			}
		} catch (e) {
			return str;
		}
	}

	function parseArguments(str: string): Record<string, unknown> | null {
		try {
			const parsed = parseJSONString(str);
			if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
				return parsed as Record<string, unknown>;
			}
			return null;
		} catch {
			return null;
		}
	}

	function isToolResultError(value: unknown): boolean {
		if (typeof value === 'string') {
			const text = value.trim().toLowerCase();
			if (
				text.startsWith('error:') ||
				text.startsWith('exception:') ||
				text.startsWith('traceback') ||
				text.startsWith('http error!')
			) {
				return true;
			}
		}

		let parsed = value;
		while (typeof parsed === 'string') {
			try {
				parsed = JSON.parse(parsed);
			} catch {
				break;
			}
		}
		if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return false;

		const result = parsed as Record<string, unknown>;
		const error = result.error;
		if (
			(typeof error === 'string' && error.trim().length > 0) ||
			(typeof error === 'object' && error !== null)
		) {
			return true;
		}

		const status = typeof result.status === 'string' ? result.status.trim().toLowerCase() : '';
		if (status === 'error' || status === 'failed') return true;

		const message = result.message;
		return (
			(result.success === false || result.ok === false) &&
			((typeof message === 'string' && message.trim().length > 0) ||
				(typeof message === 'object' && message !== null))
		);
	}

	export let resultContent: string = '';
export let workerStatus: 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted' | null = null;

	$: result = resultContent || decode(attributes?.result ?? '');
	$: files = parseJSONString(decode(attributes?.files ?? ''));
	$: embeds = parseJSONString(decode(attributes?.embeds ?? ''));
	$: isAskUser = attributes?.name === 'ask_user';
	$: needsInput = !workerStatus && isAskUser && attributes?.status === 'pending';
	$: needsApproval = !workerStatus && !isAskUser && attributes?.status === 'pending' && resolvable;
	$: args =
		open || needsApproval || needsInput || (Array.isArray(embeds) && embeds.length > 0)
			? decode(attributes?.arguments ?? '')
			: '';
	$: isRejected = attributes?.status === 'rejected';
	$: isDone =
		attributes?.done === 'true' ||
		attributes?.status === 'failed' ||
		attributes?.status === 'incomplete';
	$: isExecuting = !isDone && !isRejected && attributes?.status === 'completed';
	$: isPreparing = !isDone && !isRejected && !needsApproval && !needsInput && !isExecuting;
	$: isActive = workerStatus ? workerStatus === 'running' : isPreparing || isExecuting;
	$: isError = workerStatus
		? ['failed', 'interrupted', 'cancelled'].includes(workerStatus)
		: attributes?.status === 'failed' || (isDone && isToolResultError(result));

	$: parsedArgs = parseArguments(args);
	$: parsedResult = parseJSONString(result);
	$: isWorkspaceExecTool =
		attributes?.name === 'run_workspace_command' ||
		attributes?.name === 'run_document_code' ||
		attributes?.name === 'run_skill_script';
	$: isWorkspaceSubtasksTool = attributes?.name === 'run_workspace_subtasks';
	$: execCodeLanguage =
		attributes?.name === 'run_workspace_command'
			? 'Terminal'
			: attributes?.name === 'run_document_code'
				? String(parsedArgs?.language ?? 'Code').replace(/^./, (c) => c.toUpperCase())
				: attributes?.name === 'run_skill_script'
					? `Skill: ${parsedArgs?.skill ?? ''}/${parsedArgs?.script ?? ''}`
					: '';
	$: execCodeBody =
		attributes?.name === 'run_workspace_command'
			? typeof parsedArgs?.command === 'string'
				? parsedArgs.command
				: ''
			: attributes?.name === 'run_document_code'
				? typeof parsedArgs?.code === 'string'
					? parsedArgs.code
					: ''
				: attributes?.name === 'run_skill_script' && Array.isArray(parsedArgs?.args)
					? parsedArgs.args.join(' ')
					: '';
	$: execStructuredResult =
		isWorkspaceExecTool &&
		typeof parsedResult === 'object' &&
		parsedResult !== null &&
		('exit_code' in parsedResult || 'stdout' in parsedResult || 'stderr' in parsedResult)
			? (parsedResult as Record<string, unknown>)
			: null;
	$: subtasksStructuredResult =
		isWorkspaceSubtasksTool &&
		typeof parsedResult === 'object' &&
		parsedResult !== null &&
		'total' in parsedResult
			? (parsedResult as Record<string, unknown>)
			: null;
	$: deliverableDownloadUrl =
		isDone &&
		!isError &&
		typeof parsedResult === 'object' &&
		parsedResult !== null &&
		typeof parsedResult.download_url === 'string' &&
		parsedResult.download_url.startsWith('/api/v1/')
			? `${WEAI_BASE_URL}${parsedResult.download_url}`
			: null;
	$: deliverableFilename =
		deliverableDownloadUrl && typeof parsedResult?.filename === 'string' && parsedResult.filename
			? parsedResult.filename
			: 'Download file';

	const toggleOpen = () => {
		open = !open;
	};

	const toggleOpenOnKeydown = (event: KeyboardEvent) => {
		if (event.target !== event.currentTarget) return;
		if (event.key !== 'Enter' && event.key !== ' ') {
			return;
		}

		event.preventDefault();
		toggleOpen();
	};
</script>

<div {id} class={className}>
	{#if allowEmbeds && !grouped && embeds && Array.isArray(embeds) && embeds.length > 0}
		<!-- Embed Mode: Show iframes without collapsible behavior -->
		<div class="py-1 w-full cursor-pointer">
			<div class="w-full text-xs text-gray-500">
				{attributes.name}
			</div>
			{#each embeds as embed, idx}
				<div class="my-2" id={`${componentId}-tool-call-embed-${idx}`}>
					{#if typeof embed === 'object' && embed?.type === 'mcp_app'}
						{#key embed}<MCPApp app={embed} />{/key}
					{:else if typeof embed === 'string'}
						<FullHeightIframe
							src={embed}
							{args}
							allowScripts={true}
							allowForms={$settings?.iframeSandboxAllowForms ?? false}
							allowSameOrigin={$settings?.iframeSandboxAllowSameOrigin ?? false}
							allowPopups={true}
						/>
					{/if}
				</div>
			{/each}
		</div>
	{:else}
		<!-- Tool call display -->
		<div
			class="{buttonClassName} w-full min-w-0 cursor-pointer"
			role="button"
			tabindex="0"
			aria-expanded={open}
			on:click={toggleOpen}
			on:keydown={toggleOpenOnKeydown}
		>
			<div
				class="w-full min-w-0 max-w-full font-normal flex items-center gap-1.5 {isActive
					? 'shimmer'
					: ''}"
			>
				<!-- Status icon -->
				{#if isActive}
					<div>
						<Spinner className="size-4" />
					</div>
				{:else if isRejected}
					<div class="text-red-400 dark:text-red-500">
						<XMark className="size-4" strokeWidth="2.5" />
					</div>
				{:else if isError}
					<div class="text-red-500 dark:text-red-400">
						<XMark className="size-4" strokeWidth="2.5" />
					</div>
				{:else if isDone}
					<div class="text-emerald-500 dark:text-emerald-400">
						<CheckCircle className="size-4" strokeWidth="2" />
					</div>
				{:else}
					<div class="text-gray-400 dark:text-gray-500">
						<WrenchSolid className="size-3.5" />
					</div>
				{/if}

				<!-- Label -->
				<div class="flex-1 min-w-0 line-clamp-1">
					<!-- Short label (below md) -->
					<span class="@md:hidden text-black dark:text-white">{displayName}</span>
					<!-- Full label (md and above) -->
					<span class="hidden @md:inline font-normal">
						{#if workerStatus}
							{displayName}
						{:else if isRejected}
							{$i18n.t('Denied {{NAME}}', { NAME: displayName })}
						{:else if isDone}
							{$i18n.t('View Result from {{NAME}}', { NAME: displayName })}
						{:else if needsInput}
							{$i18n.t('Input needed')}
						{:else if needsApproval}
							{$i18n.t('Allow {{NAME}}?', { NAME: displayName })}
						{:else if isPreparing}
							{$i18n.t('Preparing {{NAME}}...', { NAME: displayName })}
						{:else}
							{$i18n.t('Executing {{NAME}}...', { NAME: displayName })}
						{/if}
					</span>
				</div>

				{#if needsApproval && !isAskUser}
					<span class="flex gap-1 shrink-0">
						<button
							type="button"
							class="tool-call-allow-button text-[0.6875rem] px-2.5 py-0.5 rounded-md text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-white/8 hover:bg-gray-200 dark:hover:bg-white/12 transition-colors duration-100 disabled:opacity-50"
							disabled={resolving}
							on:click|stopPropagation={() => onResolve(true)}
						>
							{$i18n.t('Allow')}
						</button>
						<button
							type="button"
							class="tool-call-deny-button text-[0.6875rem] px-2 py-0.5 rounded-md text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 disabled:opacity-50"
							disabled={resolving}
							on:click|stopPropagation={() => onResolve(false)}
						>
							{$i18n.t('Deny')}
						</button>
					</span>
				{:else}
					<!-- Chevron -->
					<div class="flex shrink-0 self-center translate-y-[1px]">
						{#if open}
							<ChevronUp strokeWidth="3.5" className="size-3" />
						{:else}
							<ChevronDown strokeWidth="3.5" className="size-3" />
						{/if}
					</div>
				{/if}
			</div>
		</div>

		{#if open}
			<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
				<div
					class="border border-gray-50 dark:border-gray-850/30 rounded-2xl my-1.5 p-2.5 space-y-2"
				>
					{#if $$slots.details}
						<slot name="details" />
					{:else}
					{#if args}
						<!-- Input -->
						<div>
							<div
								class="flex items-center justify-between text-[0.625rem] uppercase tracking-wider font-normal text-gray-400 dark:text-gray-500 mb-1.5 px-1"
							>
								<span>{isWorkspaceExecTool && execCodeLanguage ? execCodeLanguage : $i18n.t('Input')}</span>
								{#if isWorkspaceExecTool && parsedArgs?.timeout_seconds}
									<span>{parsedArgs.timeout_seconds}s</span>
								{/if}
							</div>

							{#if isWorkspaceExecTool && execCodeBody}
								<div class="tool-call-body w-full max-w-none!">
									<pre
										class="text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-words font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2.5 overflow-x-auto">{execCodeBody}</pre>
								</div>
							{:else if parsedArgs}
								<div class="px-1 space-y-0.5">
									{#each Object.entries(parsedArgs) as [key, value]}
										<div class="flex gap-2 text-xs py-0.5">
											<span class="font-normal text-gray-600 dark:text-gray-400 shrink-0"
												>{key}</span
											>
											<span class="text-gray-800 dark:text-gray-200 break-all"
												>{typeof value === 'object' ? JSON.stringify(value) : value}</span
											>
										</div>
									{/each}
								</div>
							{:else}
								<div class="tool-call-body w-full max-w-none!">
									<pre
										class="text-xs text-gray-600 dark:text-gray-300 whitespace-pre font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2 overflow-x-auto">{formatJSONString(
											args
										)}</pre>
								</div>
							{/if}
						</div>
					{/if}

					<!-- Output -->
					{#if isDone && result}
						<div>
							<div
								class="flex items-center justify-between text-[0.625rem] uppercase tracking-wider font-normal text-gray-400 dark:text-gray-500 mb-1.5 px-1"
							>
								<span>{$i18n.t('Output')}</span>
								{#if execStructuredResult && execStructuredResult.exit_code !== undefined}
									<span
										class="px-1.5 py-0.5 rounded font-mono {execStructuredResult.exit_code === 0
											? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300'
											: 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300'}"
									>
										exit {execStructuredResult.exit_code}
									</span>
								{/if}
							</div>
							<div class="w-full max-w-none! space-y-2">
								{#if execStructuredResult}
									{#if typeof execStructuredResult.error === 'string' && execStructuredResult.error}
										<pre
											class="text-xs text-red-600 dark:text-red-300 whitespace-pre-wrap break-words font-mono bg-red-50/70 dark:bg-red-950/30 rounded-lg p-2 overflow-x-auto">{execStructuredResult.error}</pre>
									{/if}
									{#if typeof execStructuredResult.stdout === 'string' && execStructuredResult.stdout}
										<pre
											class="text-xs text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-words font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2.5 overflow-x-auto">{execStructuredResult.stdout}</pre>
									{/if}
									{#if typeof execStructuredResult.stderr === 'string' && execStructuredResult.stderr}
										<div>
											<div
												class="text-[0.625rem] uppercase tracking-wider font-normal text-red-500 dark:text-red-400 mb-1 px-1"
											>
												{$i18n.t('Errors')}
											</div>
											<pre
												class="text-xs text-red-700 dark:text-red-300 whitespace-pre-wrap break-words font-mono bg-red-50/70 dark:bg-red-950/30 rounded-lg p-2.5 overflow-x-auto">{execStructuredResult.stderr}</pre>
										</div>
									{/if}
									{#if Array.isArray(execStructuredResult.outputs) && execStructuredResult.outputs.length > 0}
										<div class="flex flex-wrap gap-1.5 px-1 text-xs">
											{#each execStructuredResult.outputs as outPath}
												<span
													class="font-mono px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300"
													>{outPath}</span
												>
											{/each}
										</div>
									{/if}
									{#if !execStructuredResult.stdout && !execStructuredResult.stderr && !execStructuredResult.error}
										<div class="px-1 text-xs text-gray-400 dark:text-gray-500">(no output)</div>
									{/if}
								{:else if subtasksStructuredResult}
									<div class="flex flex-wrap items-center gap-1.5 px-1 text-xs">
										<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-medium">
											{subtasksStructuredResult.status ?? 'done'}
										</span>
										<span class="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
											{subtasksStructuredResult.completed ?? 0}/{subtasksStructuredResult.total ?? 0} completed
										</span>
										{#if Number(subtasksStructuredResult.skipped ?? 0) > 0}
											<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
												{subtasksStructuredResult.skipped} skipped
											</span>
										{/if}
										{#if Number(subtasksStructuredResult.failed ?? 0) > 0}
											<span class="px-2 py-0.5 rounded bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300">
												{subtasksStructuredResult.failed} failed
											</span>
										{/if}
										{#if typeof subtasksStructuredResult.report_path === 'string'}
											<span class="font-mono text-gray-500 dark:text-gray-400">
												{subtasksStructuredResult.report_path}
											</span>
										{/if}
									</div>
									{#if Array.isArray(subtasksStructuredResult.failed_items) && subtasksStructuredResult.failed_items.length > 0}
										<pre
											class="text-xs text-red-700 dark:text-red-300 whitespace-pre-wrap break-words font-mono bg-red-50/70 dark:bg-red-950/30 rounded-lg p-2 overflow-x-auto">{JSON.stringify(
												subtasksStructuredResult.failed_items,
												null,
												2
											)}</pre>
									{/if}
								{:else if typeof parsedResult === 'object' && parsedResult !== null}
									<pre
										class="text-xs text-gray-600 dark:text-gray-300 whitespace-pre font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2 overflow-x-auto">{JSON.stringify(
											parsedResult,
											null,
											2
										)}</pre>
								{:else}
									{@const resultStr = String(parsedResult)}
									{@const isTruncated = resultStr.length > RESULT_PREVIEW_LIMIT && !expandedResult}
									<pre
										class="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words font-mono">{isTruncated
											? resultStr.slice(0, RESULT_PREVIEW_LIMIT)
											: resultStr}</pre>
									{#if isTruncated}
										<button
											class="mt-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
											on:click|stopPropagation={() => {
												expandedResult = true;
											}}
										>
											{$i18n.t('Show all ({{COUNT}} characters)', {
												COUNT: resultStr.length.toLocaleString()
											})}
										</button>
									{/if}
								{/if}
							</div>
						</div>
					{/if}
					{/if}
				</div>
			</div>
		{/if}
	{/if}

	<!-- Files display (images, published deliverables) when done -->
	{#if isDone}
		{#if deliverableDownloadUrl}
			<div class="mt-1.5 flex items-center gap-2 text-xs">
				<a
					href={deliverableDownloadUrl}
					download={deliverableFilename}
					target="_blank"
					rel="noopener noreferrer"
					class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 dark:border-gray-700 px-2.5 py-1 text-gray-800 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
				>
					<span>{deliverableFilename}</span>
				</a>
			</div>
		{/if}
		{#if typeof files === 'object'}
			{#each files ?? [] as file, idx}
				{#if typeof file === 'string'}
					{#if file.startsWith('data:image/')}
						<Image id={`${componentId}-tool-call-result-${idx}`} src={file} alt="Image" />
					{/if}
				{:else if typeof file === 'object'}
					{#if (file.type === 'image' || (file?.content_type ?? '').startsWith('image/')) && file.url}
						<Image id={`${componentId}-tool-call-result-${idx}`} src={file.url} alt="Image" />
					{/if}
				{/if}
			{/each}
		{/if}
	{/if}
</div>
