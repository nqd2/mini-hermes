export type ChatTaskTicket = Readonly<{
	chatId: string | null;
	generation: number;
	taskIdsVersion: number;
}>;

export type ChatTaskState<T = unknown> = Readonly<{
	chatId: string | null;
	taskIds: string[] | null;
	tasks: T[];
}>;

/** Owns the task state and async update scope for the currently active chat. */
export const createChatTaskLifecycle = <T = unknown>() => {
	let generation = 0;
	let taskIdsVersion = 0;
	let state: ChatTaskState<T> = { chatId: null, taskIds: null, tasks: [] };
	const listeners = new Set<(state: ChatTaskState<T>) => void>();

	const snapshot = (): ChatTaskState<T> => ({
		chatId: state.chatId,
		taskIds: state.taskIds ? [...state.taskIds] : null,
		tasks: [...state.tasks]
	});

	const publish = () => {
		const current = snapshot();
		for (const listener of listeners) listener(current);
	};

	const isCurrent = (ticket: ChatTaskTicket) =>
		ticket.generation === generation && ticket.chatId === state.chatId;

	const update = (patch: Partial<ChatTaskState<T>>) => {
		state = { ...state, ...patch };
		publish();
	};

	return {
		get state() {
			return snapshot();
		},
		subscribe(listener: (state: ChatTaskState<T>) => void) {
			listeners.add(listener);
			listener(snapshot());
			return () => listeners.delete(listener);
		},
		begin(chatId: string | null): ChatTaskTicket {
			generation += 1;
			if (state.chatId !== chatId) {
				taskIdsVersion += 1;
				state = { chatId, taskIds: null, tasks: [] };
				publish();
			}
			return { chatId, generation, taskIdsVersion };
		},
		isCurrent,
		setTaskIds(ids: string[] | null | undefined, ticket?: ChatTaskTicket) {
			if (ticket && (!isCurrent(ticket) || ticket.taskIdsVersion !== taskIdsVersion)) return false;
			taskIdsVersion += 1;
			update({ taskIds: Array.isArray(ids) && ids.length > 0 ? [...ids] : null });
			return true;
		},
		appendTaskIds(ids: string[] | null | undefined) {
			if (!ids?.length) return;
			taskIdsVersion += 1;
			update({ taskIds: [...(state.taskIds ?? []), ...ids] });
		},
		setTasks(tasks: T[] | null | undefined, ticket?: ChatTaskTicket) {
			if (ticket && !isCurrent(ticket)) return false;
			update({ tasks: Array.isArray(tasks) ? [...tasks] : [] });
			return true;
		},
		clear(chatId: string | null = null) {
			generation += 1;
			taskIdsVersion += 1;
			state = { chatId, taskIds: null, tasks: [] };
			publish();
		}
	};
};
