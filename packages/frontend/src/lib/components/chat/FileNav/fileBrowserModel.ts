import type { FileEntry } from '$lib/apis/terminal';

export type SortMode = 'name' | 'size' | 'date' | 'created';

export type BrowserRow = FileEntry & {
	fullPath: string;
	parentPath: string;
	depth: number;
	rowIndex: number;
};

export const normalizePath = (path: string) => path.replace(/\\/g, '/').replace(/\/{2,}/g, '/');

export const cleanEntryName = (name: string) =>
	normalizePath(name).split('/').filter(Boolean).at(-1) ?? name.replace(/\/+$/, '');

export const normalizeEntries = (items: FileEntry[]) =>
	items.map((entry) => ({ ...entry, name: cleanEntryName(entry.name) }));

export const asDirectoryPath = (path: string) => {
	const normalized = normalizePath(path || '/');
	if (normalized === '/') return '/';
	return normalized.endsWith('/') ? normalized : `${normalized}/`;
};

export const joinPath = (parentPath: string, name: string) => {
	const parent = asDirectoryPath(parentPath);
	const child = cleanEntryName(name);
	if (!child) return parent;
	return normalizePath(`${parent}${child}`);
};

export const sortEntries = (items: FileEntry[], mode: SortMode, asc: boolean): FileEntry[] => {
	return [...items].sort((a, b) => {
		// Directories always first
		if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
		if (mode === 'created') {
			// Older servers and some filesystems do not expose creation time.
			if (a.created == null && b.created != null) return 1;
			if (a.created != null && b.created == null) return -1;
			const cmp = (a.created ?? 0) - (b.created ?? 0) || a.name.localeCompare(b.name);
			return asc ? cmp : -cmp;
		}
		if (mode === 'date') {
			if (a.modified !== undefined && b.modified !== undefined) {
				return asc ? a.modified - b.modified : b.modified - a.modified;
			}
			const cmp = a.name.localeCompare(b.name);
			return asc ? cmp : -cmp;
		}
		if (mode === 'size') {
			if (a.size !== undefined && b.size !== undefined) {
				return asc ? a.size - b.size : b.size - a.size;
			}
			const cmp = a.name.localeCompare(b.name);
			return asc ? cmp : -cmp;
		}
		const cmp = a.name.localeCompare(b.name);
		return asc ? cmp : -cmp;
	});
};

export const filterEntries = (items: FileEntry[], hiddenVisible: boolean) =>
	hiddenVisible ? items : items.filter((entry) => !entry.name.startsWith('.'));

export const entryPath = (parentPath: string, entry: FileEntry) =>
	entry.type === 'directory'
		? asDirectoryPath(joinPath(parentPath, entry.name))
		: joinPath(parentPath, entry.name);

export const withRowIndexes = (rows: Omit<BrowserRow, 'rowIndex'>[]): BrowserRow[] =>
	rows.map((row, rowIndex) => ({ ...row, rowIndex }));

export const buildVisibleRows = (
	items: FileEntry[],
	parentPath: string,
	expanded: Set<string>,
	cache: Map<string, FileEntry[]>,
	hiddenVisible: boolean,
	mode: SortMode,
	asc: boolean,
	depth = 0
): Omit<BrowserRow, 'rowIndex'>[] =>
	sortEntries(filterEntries(items, hiddenVisible), mode, asc).flatMap((entry) => {
		const fullPath = entryPath(parentPath, entry);
		const row = { ...entry, fullPath, parentPath, depth };
		const children =
			entry.type === 'directory' && expanded.has(fullPath)
				? buildVisibleRows(
						cache.get(fullPath) ?? [],
						fullPath,
						expanded,
						cache,
						hiddenVisible,
						mode,
						asc,
						depth + 1
					)
				: [];
		return [row, ...children];
	});
