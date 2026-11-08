export type FileSelectionResult = {
	selectedPaths: Set<string>;
	lastClickedIndex: number | null;
};

export const selectAllFilePaths = (paths: string[]) => new Set(paths);

export const toggleFileSelection = (
	selectedPaths: ReadonlySet<string>,
	visibleRows: readonly { fullPath: string }[],
	selectedPath: string,
	shiftKey: boolean,
	lastClickedIndex: number | null,
	selectedIndex = visibleRows.findIndex((row) => row.fullPath === selectedPath)
): FileSelectionResult => {
	if (selectedIndex < 0) {
		return { selectedPaths: new Set(selectedPaths), lastClickedIndex };
	}

	const next = new Set(selectedPaths);
	const remove = next.has(selectedPath);
	const range = shiftKey && !remove && lastClickedIndex !== null;
	const from = range ? Math.min(lastClickedIndex, selectedIndex) : selectedIndex;
	const to = range ? Math.max(lastClickedIndex, selectedIndex) : selectedIndex;

	for (let index = from; index <= to; index++) {
		const path = visibleRows[index]?.fullPath;
		if (!path) continue;
		if (remove) next.delete(path);
		else next.add(path);
	}

	return {
		selectedPaths: next,
		lastClickedIndex: !shiftKey || lastClickedIndex === null ? selectedIndex : lastClickedIndex
	};
};
