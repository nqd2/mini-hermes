export type ChatSubmissionPreparationOptions<THistory> = {
	history: THistory;
	isCurrentHistory: (history: THistory) => boolean;
	resolveChatId: (history: THistory) => Promise<string | null>;
	ensureChatId: (history: THistory) => Promise<string>;
	hasPendingFiles: () => boolean;
	uploadPendingFiles: (sessionId: string) => Promise<boolean>;
};

export type ChatSubmissionPreparationResult =
	| { status: 'ready' }
	| { status: 'stale' }
	| { status: 'missing-chat' }
	| { status: 'upload-failed' };

/** Resolves the active chat and finishes pending uploads before a message is submitted. */
export const prepareChatSubmission = async <THistory>({
	history,
	isCurrentHistory,
	resolveChatId,
	ensureChatId,
	hasPendingFiles,
	uploadPendingFiles,
}: ChatSubmissionPreparationOptions<THistory>): Promise<ChatSubmissionPreparationResult> => {
	if (!isCurrentHistory(history)) return { status: 'stale' };

	const chatId = await resolveChatId(history);
	if (chatId === null) return { status: 'missing-chat' };
	if (!isCurrentHistory(history)) return { status: 'stale' };

	if (hasPendingFiles()) {
		const uploadSessionId = chatId || (await ensureChatId(history));
		if (!isCurrentHistory(history)) return { status: 'stale' };
		if (!(await uploadPendingFiles(uploadSessionId)))
			return { status: 'upload-failed' };
		if (!isCurrentHistory(history)) return { status: 'stale' };
	}

	return { status: 'ready' };
};
