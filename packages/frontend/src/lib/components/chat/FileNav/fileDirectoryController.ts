import type { TerminalFileList } from '$lib/apis/terminal';
import type { FileNavRequestTicket, FileNavScopedRequestTicket } from './fileNavRequestLifecycle';

export type FileDirectoryTerminal = { url: string; key: string };
export type FileDirectoryScope = {
	terminalId: string | null;
	chatId: string | null;
};
export type FileDirectoryLoadOutcome =
	{ status: 'loaded'; result: TerminalFileList } | { status: 'failed' } | { status: 'stale' };

type FileNavigationLifecycle = {
	begin: (terminalId: string | null, chatId: string | null) => FileNavRequestTicket;
	beginScoped: (
		key: string,
		terminalId: string | null,
		chatId: string | null
	) => FileNavScopedRequestTicket;
	isCurrent: (
		ticket: FileNavRequestTicket,
		terminalId: string | null,
		chatId: string | null
	) => boolean;
	isScopedCurrent: (
		ticket: FileNavScopedRequestTicket,
		terminalId: string | null,
		chatId: string | null
	) => boolean;
};

type Dependencies = {
	lifecycle: FileNavigationLifecycle;
	getScope: () => FileDirectoryScope;
	listFiles: (
		url: string,
		key: string,
		path: string,
		sessionId?: string
	) => Promise<TerminalFileList | null>;
	setCwd: (url: string, key: string, path: string, sessionId?: string) => unknown;
	onCurrentRequest?: (ticket: FileNavRequestTicket) => void;
	onExpandedLoading?: (path: string, loading: boolean) => void;
};

export const createFileDirectoryController = ({
	lifecycle,
	getScope,
	listFiles,
	setCwd,
	onCurrentRequest = () => {},
	onExpandedLoading = () => {}
}: Dependencies) => {
	const beginCurrentRequest = () => {
		const { terminalId, chatId } = getScope();
		const ticket = lifecycle.begin(terminalId, chatId);
		onCurrentRequest(ticket);
		return ticket;
	};

	const loadCurrent = async (
		ticket: FileNavRequestTicket,
		{
			terminal,
			path,
			sessionId,
			initialFile
		}: {
			terminal: FileDirectoryTerminal;
			path: string;
			sessionId?: string;
			initialFile: boolean;
		}
	): Promise<FileDirectoryLoadOutcome> => {
		let result: TerminalFileList | null;
		try {
			result = await listFiles(terminal.url, terminal.key, path, sessionId);
		} catch {
			const scope = getScope();
			return lifecycle.isCurrent(ticket, scope.terminalId, scope.chatId)
				? { status: 'failed' }
				: { status: 'stale' };
		}
		const scope = getScope();
		if (!lifecycle.isCurrent(ticket, scope.terminalId, scope.chatId)) return { status: 'stale' };

		// Match FileNav's existing behavior: update cwd after the listing request,
		// including when the listing endpoint returns null.
		if (!initialFile) void setCwd(terminal.url, terminal.key, path, sessionId);
		return result === null ? { status: 'failed' } : { status: 'loaded', result };
	};

	const loadExpanded = async (
		path: string,
		terminal: FileDirectoryTerminal,
		sessionId?: string
	): Promise<FileDirectoryLoadOutcome> => {
		const { terminalId, chatId } = getScope();
		const ticket = lifecycle.beginScoped(`directory:${path}`, terminalId, chatId);
		onExpandedLoading(path, true);
		let result: TerminalFileList | null;
		try {
			result = await listFiles(terminal.url, terminal.key, path, sessionId);
		} finally {
			const scope = getScope();
			if (lifecycle.isScopedCurrent(ticket, scope.terminalId, scope.chatId)) {
				onExpandedLoading(path, false);
			}
		}

		const scope = getScope();
		if (!lifecycle.isScopedCurrent(ticket, scope.terminalId, scope.chatId))
			return { status: 'stale' };
		return result === null ? { status: 'failed' } : { status: 'loaded', result };
	};

	return { beginCurrentRequest, loadCurrent, loadExpanded };
};
