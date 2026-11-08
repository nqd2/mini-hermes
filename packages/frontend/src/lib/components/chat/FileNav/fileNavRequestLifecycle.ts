export type FileNavRequestTicket = Readonly<{
	generation: number;
	terminalId: string | null;
	chatId: string | null;
}>;
export type FileNavScopedRequestTicket = Readonly<{
	key: string;
	generation: number;
	epoch: number;
	terminalId: string | null;
	chatId: string | null;
}>;

/** Owns stale-response invalidation for file navigation and previews. */
export const createFileNavRequestLifecycle = () => {
	let generation = 0;
	let terminalId: string | null = null;
	let chatId: string | null = null;
	let scopedEpoch = 0;
	const scopedGenerations = new Map<string, number>();

	return {
		get version() {
			return generation;
		},
		begin(nextTerminalId: string | null, nextChatId: string | null): FileNavRequestTicket {
			generation += 1;
			terminalId = nextTerminalId;
			chatId = nextChatId;
			return { generation, terminalId, chatId };
		},
		beginScoped(
			key: string,
			nextTerminalId: string | null,
			nextChatId: string | null
		): FileNavScopedRequestTicket {
			const scopedGeneration = (scopedGenerations.get(key) ?? 0) + 1;
			scopedGenerations.set(key, scopedGeneration);
			return {
				key,
				generation: scopedGeneration,
				epoch: scopedEpoch,
				terminalId: nextTerminalId,
				chatId: nextChatId
			};
		},
		isCurrent(
			ticket: FileNavRequestTicket,
			currentTerminalId: string | null,
			currentChatId: string | null
		) {
			return (
				ticket.generation === generation &&
				ticket.terminalId === terminalId &&
				ticket.chatId === chatId &&
				currentTerminalId === terminalId &&
				currentChatId === chatId
			);
		},
		isScopedCurrent(
			ticket: FileNavScopedRequestTicket,
			currentTerminalId: string | null,
			currentChatId: string | null
		) {
			return (
				ticket.epoch === scopedEpoch &&
				scopedGenerations.get(ticket.key) === ticket.generation &&
				ticket.terminalId === currentTerminalId &&
				ticket.chatId === currentChatId
			);
		},
		invalidate() {
			generation += 1;
			scopedEpoch += 1;
			scopedGenerations.clear();
			return generation;
		}
	};
};
