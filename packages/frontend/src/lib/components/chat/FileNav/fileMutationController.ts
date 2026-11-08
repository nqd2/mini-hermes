import { asDirectoryPath, joinPath, normalizePath } from './fileBrowserModel';

export type FileMutationTerminal = { url: string; key: string };

export type FileMutationResult =
	| { status: 'blocked' }
	| { status: 'invalid' }
	| { status: 'failed'; error: string }
	| { status: 'moved'; source: string; destination: string; name: string }
	| { status: 'renamed'; destination: string; name: string };

type MoveEntryResult =
	{ source: string; destination: string } | { error: string };
type PathAdapter = (
	url: string,
	key: string,
	path: string,
	sessionId?: string,
) => Promise<unknown>;
type FileUploadAdapter = (
	url: string,
	key: string,
	path: string,
	file: File,
	sessionId?: string,
) => Promise<unknown>;

type Dependencies = {
	getTerminal: () => FileMutationTerminal | null;
	getSessionId: () => string | undefined;
	getCurrentPath: () => string;
	canWrite: () => boolean;
	createDirectory: PathAdapter;
	uploadFile: FileUploadAdapter;
	deleteEntry: PathAdapter;
	moveEntry: (
		url: string,
		key: string,
		source: string,
		destination: string,
		sessionId?: string,
	) => Promise<MoveEntryResult>;
};

export const createFileMutationController = ({
	getTerminal,
	getSessionId,
	getCurrentPath,
	canWrite,
	createDirectory,
	uploadFile,
	deleteEntry,
	moveEntry,
}: Dependencies) => {
	const executeMove = async (source: string, destination: string) => {
		const terminal = getTerminal();
		if (!terminal || !canWrite()) return { status: 'blocked' } as const;

		const result = await moveEntry(
			terminal.url,
			terminal.key,
			source,
			destination,
			getSessionId(),
		);
		return 'error' in result
			? ({ status: 'failed', error: result.error } as const)
			: null;
	};

	const move = async (
		source: string,
		destinationFolder: string,
	): Promise<FileMutationResult> => {
		const cleanSource = normalizePath(source).replace(/\/$/, '');
		const name = cleanSource.split('/').pop() ?? '';
		const destination = joinPath(destinationFolder, name);
		if (!name || cleanSource === destination) return { status: 'invalid' };

		const sourceDirectory = asDirectoryPath(cleanSource);
		if (asDirectoryPath(destinationFolder).startsWith(sourceDirectory)) {
			return { status: 'invalid' };
		}

		const outcome = await executeMove(cleanSource, destination);
		if (outcome) return outcome;
		return { status: 'moved', source: cleanSource, destination, name };
	};

	const rename = async (
		oldPath: string,
		newName: string,
	): Promise<FileMutationResult> => {
		if (!newName) return { status: 'blocked' };

		const directory =
			oldPath.substring(0, oldPath.lastIndexOf('/') + 1) || getCurrentPath();
		const destination = `${directory}${newName}`;
		if (oldPath === destination) return { status: 'invalid' };

		const outcome = await executeMove(oldPath, destination);
		if (outcome) return outcome;
		return { status: 'renamed', destination, name: newName };
	};

	const createFolder = async (
		terminal: FileMutationTerminal,
		path: string,
		sessionId?: string,
	) =>
		Boolean(await createDirectory(terminal.url, terminal.key, path, sessionId));

	const createFile = async (
		terminal: FileMutationTerminal,
		path: string,
		file: File,
		sessionId?: string,
	) =>
		Boolean(
			await uploadFile(terminal.url, terminal.key, path, file, sessionId),
		);

	const deletePath = async (
		terminal: FileMutationTerminal,
		path: string,
		sessionId?: string,
	) => Boolean(await deleteEntry(terminal.url, terminal.key, path, sessionId));

	return { move, rename, createFolder, createFile, deletePath };
};
