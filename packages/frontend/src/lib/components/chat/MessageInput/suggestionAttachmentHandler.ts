type AttachmentCollection<T> = {
	addIfMissing: (item: T, matches: (existing: T) => boolean) => boolean;
};

type SuggestionAttachmentHandlerOptions<T> = {
	readFiles: () => readonly T[];
	collection: AttachmentCollection<T>;
	includeFilesystem?: boolean;
	onUpload: (event: any) => void;
};

/** Normalizes suggestion picker events into the composer attachment flow. */
export const createSuggestionAttachmentHandler = <T extends Record<string, any>>({
	readFiles,
	collection,
	includeFilesystem = false,
	onUpload
}: SuggestionAttachmentHandlerOptions<T>) => {
	return (event: any) => {
		const { type, data } = event ?? {};

		if (type === 'file' && data && typeof data === 'object') {
			collection.addIfMissing(
				{ ...data, status: 'processed' } as T,
				(existing) => existing.id === data.id
			);
			return;
		}

		if (type === 'filesystem' && includeFilesystem) {
			const file = data && typeof data === 'object' ? data : {};
			const path =
				file.path ?? file.url ?? file.id ?? (typeof data === 'string' ? data : undefined);
			if (!path) return;

			collection.addIfMissing(
				{
					...file,
					type: 'filesystem',
					id: path,
					path,
					url: path,
					name: file.name,
					size: file.size,
					status: 'processed'
				} as T,
				(existing) =>
					existing.type === 'filesystem' && (existing.path ?? existing.url ?? existing.id) === path
			);
			return;
		}

		if (readFiles().some((file) => file.url === data || file.name === data)) return;
		onUpload(event);
	};
};
