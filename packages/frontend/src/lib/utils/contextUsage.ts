export type ContextMessage = {
	content?: unknown;
	output?: unknown[];
	tool_calls?: unknown;
	files?: unknown[];
	usage?: Record<string, unknown>;
	info?: { usage?: Record<string, unknown> };
	contextSummary?: unknown;
	context_summary?: unknown;
};

export const estimateTokens = (value: unknown): number => {
	if (value === null || value === undefined || value === '') return 0;
	if (typeof value !== 'string') {
		try {
			value = JSON.stringify(value);
		} catch {
			value = String(value);
		}
	}
	return Math.max(1, Math.floor(String(value).length / 4));
};

export const estimateMessagesTokens = (messages: ContextMessage[]): number =>
	messages.reduce((total, message) => {
		// Structured output already includes the assistant text and tool calls.
		let next = total + 4;
		next += message.output?.length
			? estimateTokens(message.output)
			: estimateTokens(message.content) + estimateTokens(message.tool_calls);
		if (message.files?.length) {
			next += estimateTokens(
				JSON.stringify(message.files).replace(/data:[\w/+.;=%-]*;base64,[A-Za-z0-9+/=]*/g, '')
			);
		}
		return next;
	}, 0);

// Usage objects survive message copies between streaming events. Weak keys let old
// checkpoints disappear when a new usage snapshot or chat replaces them.
const usageCheckpoints = new WeakMap<object, number>();

export const captureUsageCheckpoint = (message: ContextMessage): void => {
	const usage = message.usage ?? message.info?.usage;
	if (usage) usageCheckpoints.set(usage, estimateMessagesTokens([message]));
};

export const estimateUsageGrowth = (message: ContextMessage): number => {
	const usage = message.usage ?? message.info?.usage;
	if (!usage) return 0;
	const tokens = estimateMessagesTokens([message]);
	if (!usageCheckpoints.has(usage)) {
		// shortcut: loaded chats anchor here; persist checkpoints if reloads must retain in-flight estimates.
		usageCheckpoints.set(usage, tokens);
	}
	return Math.max(0, tokens - usageCheckpoints.get(usage)!);
};

export const estimateContextUsage = (
	messages: ContextMessage[],
	system: string,
	threshold: number | null,
	getUsageTokenCount: (usage?: Record<string, unknown> | null) => number
) => {
	let summary = '';
	let startIdx = 0;
	for (let idx = 0; idx < messages.length; idx += 1) {
		const value = messages[idx]?.contextSummary ?? messages[idx]?.context_summary;
		if (typeof value === 'string' && value.trim()) {
			summary = value;
			startIdx = idx;
		}
	}

	const activeMessages = messages.slice(startIdx);
	let estimatedTokens = estimateTokens(system);
	let hasUsageCheckpoint = false;

	for (let idx = activeMessages.length - 1; idx >= 0; idx -= 1) {
		const usage = activeMessages[idx]?.usage ?? activeMessages[idx]?.info?.usage;
		const usageTokens = getUsageTokenCount(usage);
		if (usageTokens) {
			hasUsageCheckpoint = true;
			estimatedTokens =
				usageTokens +
				estimateUsageGrowth(activeMessages[idx]) +
				estimateMessagesTokens(activeMessages.slice(idx + 1));
			break;
		}
	}

	if (!hasUsageCheckpoint) {
		estimatedTokens += estimateTokens(summary) + estimateMessagesTokens(activeMessages);
	}

	return {
		tokens: estimatedTokens,
		estimated_tokens: estimatedTokens,
		threshold,
		percent:
			threshold !== null && threshold > 0
				? Math.max(0, Math.round((estimatedTokens / threshold) * 100))
				: null,
		source: 'estimated' as const
	};
};
