type DraftStorage = Pick<Storage, 'setItem' | 'removeItem'>;

export function prepareChatInputDraft<T extends { files: any[] }>(input: T): T {
	return {
		...input,
		files: input.files
			.filter((file) => file.type !== 'image' && !(file.file instanceof File))
			.map((file) => ({ ...file, user: undefined, access_grants: undefined }))
	};
}

export function createChatDraftController(storage: DraftStorage) {
	let pendingWrite: ReturnType<typeof setTimeout> | null = null;

	function cancelPendingWrite() {
		if (pendingWrite) clearTimeout(pendingWrite);
		pendingWrite = null;
	}

	function keyFor(chatId: string | null) {
		return `chat-input${chatId ? `-${chatId}` : ''}`;
	}

	async function save(draft: { prompt: string | null }, chatId: string | null = null, debounce = true) {
		cancelPendingWrite();
		const key = keyFor(chatId);
		if (draft.prompt !== null && draft.prompt.length < 5000) {
			const write = () => storage.setItem(key, JSON.stringify(draft));
			if (debounce) pendingWrite = setTimeout(write, 500);
			else write();
		} else {
			storage.removeItem(key);
		}
	}

	async function clear(chatId: string | null = null) {
		cancelPendingWrite();
		storage.removeItem(keyFor(chatId));
	}

	return { save, clear };
}
