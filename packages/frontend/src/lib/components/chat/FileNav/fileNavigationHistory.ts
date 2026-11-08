export type FileNavigationEntry = { path: string; file: string | null };
export type FileNavigationHistoryState = {
	entries: FileNavigationEntry[];
	index: number;
};

/** Owns path/file history while keeping the component's reactive state in sync. */
export const createFileNavigationHistory = (
	onChange: (state: FileNavigationHistoryState) => void = () => {}
) => {
	let state: FileNavigationHistoryState = { entries: [], index: -1 };
	const publish = (next: FileNavigationHistoryState) => {
		state = next;
		onChange({ entries: [...state.entries], index: state.index });
	};

	const step = (direction: -1 | 1) => {
		const nextIndex = state.index + direction;
		const entry = state.entries[nextIndex];
		if (!entry) return null;

		publish({ ...state, index: nextIndex });
		return { ...entry };
	};

	return {
		push(path: string, file: string | null = null, suppressed = false) {
			if (suppressed) return false;
			const current = state.entries[state.index];
			if (current?.path === path && current.file === file) return false;

			const entries = state.entries.slice(0, state.index + 1);
			entries.push({ path, file });
			publish({ entries, index: entries.length - 1 });
			return true;
		},
		back: () => step(-1),
		forward: () => step(1),
		snapshot: () => ({ entries: [...state.entries], index: state.index })
	};
};

export const navigateFileHistoryEntry = async (
	entry: FileNavigationEntry,
	{
		setBusy,
		loadDirectory,
		openFile
	}: {
		setBusy: (busy: boolean) => void;
		loadDirectory: (path: string) => Promise<unknown>;
		openFile: (path: string) => Promise<unknown>;
	}
) => {
	setBusy(true);
	try {
		await loadDirectory(entry.path);
		if (entry.file) await openFile(entry.file);
	} finally {
		setBusy(false);
	}
};
