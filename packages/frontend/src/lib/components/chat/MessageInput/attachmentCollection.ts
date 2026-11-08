/** Owns immutable mutations for the attachment array bound to MessageInput. */
export const createAttachmentCollection = <T>(
	readItems: () => readonly T[],
	writeItems: (items: T[]) => void
) => {
	const items = () => [...readItems()];

	return {
		add(item: T) {
			writeItems([...items(), item]);
		},
		addIfMissing(item: T, matches: (existing: T) => boolean) {
			if (readItems().some(matches)) return false;
			writeItems([...items(), item]);
			return true;
		},
		remove(matches: (item: T) => boolean) {
			const current = items();
			const remaining = current.filter((item) => !matches(item));
			const removedCount = current.length - remaining.length;
			if (removedCount > 0) writeItems(remaining);
			return removedCount;
		},
		removeAt(index: number) {
			const current = items();
			if (!Number.isInteger(index) || index < 0 || index >= current.length) return false;
			current.splice(index, 1);
			writeItems(current);
			return true;
		},
		touch() {
			writeItems(items());
		}
	};
};
