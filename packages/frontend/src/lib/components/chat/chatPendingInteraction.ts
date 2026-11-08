type ChatMessage = Record<string, any>;

export const parseToolArguments = (args: unknown): Record<string, unknown> => {
	if (!args) return {};
	let value: unknown = args;
	while (typeof value === 'string') {
		try {
			value = JSON.parse(value);
		} catch {
			break;
		}
	}
	return typeof value === 'object' && value !== null && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
};

export const getPendingAskUserFromMessage = (message: ChatMessage | null | undefined) => {
	if (message?.role !== 'assistant' || !Array.isArray(message.output)) return null;

	const call = message.output.find(
		(item) =>
			item?.type === 'function_call' &&
			item?.name === 'ask_user' &&
			item?.status === 'pending' &&
			(item?.call_id || item?.id)
	);

	return call ? { message, call, args: parseToolArguments(call.arguments) } : null;
};

/** Select the newest unresolved ask-user interaction visible in the active chat branch. */
export const findPendingAskUser = (
	messages: ChatMessage[] | null | undefined,
	userId: string | undefined,
	fallbackOwnerId?: string
) => {
	if (!messages) return null;
	for (const message of [...messages].reverse()) {
		if ((message.user_id ?? fallbackOwnerId ?? userId) !== userId) continue;
		const pending = getPendingAskUserFromMessage(message);
		if (pending) return pending;
	}
	return null;
};

export const messageHasPendingAskUser = (message: ChatMessage | null | undefined) =>
	Boolean(getPendingAskUserFromMessage(message));
