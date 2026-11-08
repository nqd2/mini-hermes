/** Owns the pending state of files being normalized, read, or uploaded by the composer. */
export const createFileInputLifecycle = (
	onPendingCountChange: (count: number) => void = () => {}
) => {
	let pendingCount = 0;

	const track = async <T>(operation: () => Promise<T> | T) => {
		pendingCount += 1;
		onPendingCountChange(pendingCount);

		try {
			return await operation();
		} finally {
			pendingCount -= 1;
			onPendingCountChange(pendingCount);
		}
	};

	return {
		get pendingCount() {
			return pendingCount;
		},
		get isProcessing() {
			return pendingCount > 0;
		},
		track,
		async process<T>(items: readonly T[], processItem: (item: T) => Promise<void> | void) {
			return Promise.all(
				items.map(async (item): Promise<PromiseSettledResult<void>> => {
					try {
						await track(() => processItem(item));
						return { status: 'fulfilled', value: undefined };
					} catch (reason) {
						return { status: 'rejected', reason };
					}
				})
			);
		}
	};
};
