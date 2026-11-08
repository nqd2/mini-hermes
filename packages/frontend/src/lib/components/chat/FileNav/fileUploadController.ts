export type FileUploadEntry = { path: string; file?: File };
export type FileUploadTerminal = { url: string; key: string };

type UploadAdapter = (
	url: string,
	key: string,
	path: string,
	file: File,
	sessionId?: string,
) => Promise<unknown>;

type CreateDirectoryAdapter = (
	url: string,
	key: string,
	path: string,
	sessionId?: string,
) => Promise<unknown>;

type Dependencies = {
	uploadFile: UploadAdapter;
	createDirectory: CreateDirectoryAdapter;
};

export const createFileUploadController = ({
	uploadFile,
	createDirectory,
}: Dependencies) => ({
	async upload(
		entries: FileUploadEntry[],
		terminal: FileUploadTerminal,
		destination: string,
		sessionId?: string,
	) {
		for (const entry of entries) {
			if (
				entry.path
					.split('/')
					.some(
						(part) =>
							!part || part === '.' || part === '..' || /[\\\0]/.test(part),
					)
			) {
				throw new Error(`Invalid upload path: ${entry.path}`);
			}

			const path = `${destination.replace(/\/$/, '')}/${entry.path}`;
			const result = entry.file
				? await uploadFile(
						terminal.url,
						terminal.key,
						path.slice(0, path.lastIndexOf('/')) || '/',
						entry.file,
						sessionId,
					)
				: await createDirectory(terminal.url, terminal.key, path, sessionId);
			if (!result) throw new Error(entry.path);
		}
	},
});
