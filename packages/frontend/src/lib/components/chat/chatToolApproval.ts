type ChatMessage = Record<string, any>;

/** Find the newest saved tool call that still needs an approval decision. */
export const findPendingToolApproval = (
	messagesNewestFirst: ChatMessage[],
	currentUserId: string | undefined,
	chatOwnerId?: string
) => {
	for (const message of messagesNewestFirst) {
		if ((message.user_id ?? chatOwnerId ?? currentUserId) !== currentUserId) continue;
		const output = Array.isArray(message.output) ? message.output : [];
		const resultCallIds = new Set(
			output
				.filter((item) => item?.type === 'function_call_output' && item?.call_id)
				.map((item) => item.call_id)
		);
		const pendingCall = output.find((item) => {
			const callId = item?.call_id ?? item?.id;
			return (
				item?.type === 'function_call' &&
				item?.name !== 'ask_user' &&
				(item?.status === 'pending' || item?.status === 'requires_approval') &&
				callId &&
				!resultCallIds.has(callId)
			);
		});
		const callId = pendingCall?.call_id ?? pendingCall?.id;
		if (message?.id && callId) return { message, callId };
	}
	return null;
};
