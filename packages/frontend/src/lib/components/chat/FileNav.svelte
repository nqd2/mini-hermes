<script lang="ts">
	import type { TerminalContext } from '$lib/types';
	import type { TerminalConnection } from '$lib/apis/terminal';
	import type { GitStatus } from '$lib/apis/terminal/git';
	import FileGitDiff from './FileNav/FileGitDiff.svelte';
	import { createFileNavRequestLifecycle } from './FileNav/fileNavRequestLifecycle';
	import { createFileDirectoryController } from './FileNav/fileDirectoryController';
	import { createFileMutationController } from './FileNav/fileMutationController';
	import { createFileMutationWorkflowController } from './FileNav/fileMutationWorkflowController';
	import { createFileDownloadController } from './FileNav/fileDownloadController';
	import { createFileContentMatchController } from './FileNav/fileContentMatchController';
	import { createBulkDownloadPlan, deleteSelectedFilePaths } from './FileNav/fileBulkActions';
	import { createFileUploadController, type FileUploadEntry } from './FileNav/fileUploadController';
	import { readDroppedFiles } from './FileNav/droppedFileReader';
	import { prepareFileOpenRequest } from './FileNav/fileOpenRequestController';
	import { createFileBufferProtection } from './FileNav/fileBufferProtection';
	import { createFileSaveController } from './FileNav/fileSaveController';
	import { createFileSelectionEventController } from './FileNav/fileSelectionEventController';
	import {
		createFileNavigationHistory,
		navigateFileHistoryEntry
	} from './FileNav/fileNavigationHistory';
	import { selectAllFilePaths, toggleFileSelection } from './FileNav/fileSelectionModel';
	import {
		asDirectoryPath,
		buildVisibleRows,
		cleanEntryName,
		entryPath,
		filterEntries,
		joinPath,
		normalizeEntries,
		normalizePath,
		sortEntries,
		withRowIndexes,
		type BrowserRow,
		type SortMode
	} from './FileNav/fileBrowserModel';
	import { getFileExt, loadFilePreviewData, OFFICE_EXTS } from './FileNav/filePreviewLoader';
	import { loadCurrentFilePreviewRequest } from './FileNav/filePreviewRequest';
	import { createFileMatchSearchController } from './FileNav/fileMatchSearchController';
	import { filesystemFile, type FilesystemFile } from '$lib/apis/terminal';
	import { autoUpdate, computePosition, flip, offset, shift } from '@floating-ui/dom';
	import ArrowRight from '$lib/components/icons/ArrowRight.svelte';
	import { toast } from 'svelte-sonner';
	import { getContext, onMount, onDestroy, tick } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import {
		terminalServers,
		settings,
		showFileNavPath,
		showFileNavDir,
		selectedTerminalId
	} from '$lib/stores';
	import {
		getCwd,
		getFileMatches,
		listFiles,
		downloadFileBlob,
		archiveFromTerminal,
		uploadToTerminal,
		createDirectory,
		deleteEntry,
		moveEntry,
		setCwd,
		type FileEntry,
		type TerminalContentMatch,
		type TerminalFileMatch,
		type TerminalFileRoot,
		type TerminalCwd
	} from '$lib/apis/terminal';
	import { isCodeFile } from '$lib/utils/codeHighlight';
	import { isSavedChatId, isTemporaryChatId } from '$lib/utils/chatId';
	import { copyToClipboard } from '$lib/utils';
	import { normalizeDocumentTargetPage } from '$lib/utils/documentPreview';

	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import ConfirmDialog from '../common/ConfirmDialog.svelte';
	import DropdownMenu from '../common/DropdownMenu.svelte';

	import FileNavToolbar from './FileNav/FileNavToolbar.svelte';
	import FilePreview from './FileNav/FilePreview.svelte';
	import CommentFilePreview from './FileNav/CommentFilePreview.svelte';
	import { supportsComments } from '$lib/utils/comments/types';
	import FileComparisonModal from './FileNav/FileComparisonModal.svelte';
	import FileEntryRow from './FileNav/FileEntryRow.svelte';
	import Icon from './FileNav/Icon.svelte';
	import FileTypeIcon from './FileNav/FileTypeIcon.svelte';
	import BulkActionBar from './FileNav/BulkActionBar.svelte';
	import TerminalDock from './TerminalDock.svelte';

	// Directory and preview state belong to this tab, including its terminal connection.
	let savedPath = '/';
	let savedFileRoot: TerminalFileRoot | null = null;
	const treeExpandedCache = new Map<string, string[]>();
	const treeContentsCache = new Map<string, [string, FileEntry[]][]>();

	const i18n = getContext<Readable<I18n>>('i18n');
	// Keep the error fallback discoverable by the i18n extractor.
	// t('Unknown error')

	export let overlay = false;
	export let active = true;
	export let gitStatus: GitStatus | null = null;
	export let gitConnection: TerminalConnection | null = null;
	export let gitDiffMode: 'unified' | 'split' = 'unified';
	export let gitHideWhitespace = false;
	export let terminalId: string | null = null;
	export let initialFile: { path: string; page?: number | null } | null = null;
	export let initialDirectory: string | null = null;
	export let manageFileRequests = true;
	export let showTerminalDock = true;
	export let onOpenFile: ((path: string, page?: number | null) => void) | null = null;
	export let onBrowseDirectory: ((path: string) => void) | null = null;
	$: effectiveTerminalId = terminalId ?? $selectedTerminalId;

	export let chatId: string | null = null;
	export let terminalContext: TerminalContext | null = null;
	export let onAskFile: ((instruction: string, file: FilesystemFile) => Promise<void>) | null =
		null;
	let viewerRoot: HTMLDivElement;
	let fileAsk: {
		text: string;
		location?: string;
		anchor: Range | HTMLElement;
	} | null = null;
	// Keep typing from invalidating the keyed selection and remounting the input.
	let fileAskInput = '';
	let usingFileAsk = false;
	let asking = false;
	const positionFileAsk = (node: HTMLFormElement, anchor: Range | HTMLElement) => {
		document.body.appendChild(node);
		const reference = {
			getBoundingClientRect: () => anchor.getBoundingClientRect(),
			contextElement: viewerRoot
		};
		const cleanup = autoUpdate(reference, node, () => {
			computePosition(reference, node, {
				strategy: 'fixed',
				placement: anchor instanceof Range ? 'bottom-start' : 'bottom-end',
				middleware: [offset(6), flip(), shift({ padding: 8 })]
			}).then(({ x, y }) => {
				node.style.left = `${x}px`;
				node.style.top = `${y}px`;
			});
		});
		return {
			destroy() {
				cleanup();
				node.remove();
			}
		};
	};
	const submitFileAsk = async () => {
		const request = fileAsk;
		const instruction = fileAskInput.trim();
		if (!request || !instruction || asking || !onAskFile) return;
		asking = true;
		try {
			const file = await prepareFile();
			if (fileAsk !== request) return;
			const quote = [request.location, request.text]
				.filter(Boolean)
				.join('\n')
				.split('\n')
				.map((line) => `> ${line}`)
				.join('\n');
			await onAskFile(
				request.text || request.location ? `${quote}\n\n${instruction}` : instruction,
				file
			);
			if (fileAsk === request) fileAsk = null;
		} catch (error) {
			toast.error(error instanceof Error ? error.message : String(error));
		} finally {
			asking = false;
		}
	};
	let openedFile: FilesystemFile | null = null;
	let pendingFile: FilesystemFile | null = null;
	let viewerLabel = '';
	const fileNavRequestLifecycle = createFileNavRequestLifecycle();
	let loadGeneration = 0;
	export let dirty = false;
	let diskChanged = false;
	let saveConnection: { url: string; key: string; chatId: string | null; path: string } | null =
		null;
	let dirtyChoice: {
		sending: boolean;
		resolve: (choice: 'save' | 'discard' | 'cancel') => void;
	} | null = null;
	const fileBufferProtection = createFileBufferProtection({
		hasUnsavedChanges: () => dirty || saving,
		isSaving: () => saving,
		hasPendingChoice: () => dirtyChoice !== null,
		requestChoice: async (sending) => {
			const choice = await new Promise<'save' | 'discard' | 'cancel'>((resolve) => {
				dirtyChoice = { sending, resolve };
			});
			dirtyChoice = null;
			return choice;
		},
		save: () => filePreviewRef.save(),
		isDirty: () => dirty,
		discard: () => {
			filePreviewRef?.discard();
			dirty = false;
		},
		onSaveError: (error) => toast.error(String(error))
	});
	const protectBuffer = (sending = false) => fileBufferProtection.protect(sending);
	export const requestClose = () => protectBuffer();
	export const hasUnsavedChanges = () => dirty || saving;
	export const browseDirectory = (path: string) => loadDir(path);
	export const revealPage = (page: number | null) => {
		documentTargetPage = normalizeDocumentTargetPage(page);
	};

	const publishFile = () => {
		if (!openedFile) return;
		terminalContext = {
			terminalId: openedFile.terminal_id!,
			currentDirectory: currentPath,
			file: openedFile
		};
	};
	const captureSelection = () => {
		if (!active || usingFileAsk) return;
		const selection = window.getSelection();
		if (!onAskFile || !openedFile || !selection?.rangeCount || selection.isCollapsed) return;
		const range = selection.getRangeAt(0);
		if (!viewerRoot?.contains(range.startContainer) || !viewerRoot.contains(range.endContainer))
			return;
		if (
			range.startContainer.parentElement?.closest(
				'button, select, .excel-row-num, .excel-col-hdr, .csv-row-num'
			)
		)
			return;
		const source = filePreviewRef?.getSelection();
		const text = source?.text ?? selection.toString();
		if (!text.trim()) return;
		if (new TextEncoder().encode(text).length > 16384) {
			toast.error($i18n.t('Select less text (maximum 16 KiB).'));
			return;
		}
		const page =
			range.startContainer.parentElement?.closest<HTMLElement>('[data-page-number]')?.dataset
				.pageNumber;
		const location = source?.location ?? (page ? `Page ${page}` : viewerLabel);
		fileAskInput = '';
		fileAsk = {
			text,
			location,
			anchor: range.cloneRange()
		};
	};
	const prepareFile = async (): Promise<FilesystemFile> => {
		const target = openedFile;
		if (!target) throw new Error('Wait for the file to load.');
		if (!(await protectBuffer(true))) throw new Error('File request cancelled.');
		if (
			!openedFile ||
			openedFile.path !== target.path ||
			openedFile.terminal_id !== effectiveTerminalId ||
			openedFile.session_id !== chatId
		)
			throw new Error('The file or terminal changed. Open it again before sending.');
		if (diskChanged)
			throw new Error('The file changed on disk. Reload and select the content again.');
		return structuredClone(openedFile);
	};

	$: {
		(selectedFile, effectiveTerminalId, chatId);
		fileAsk = null;
	}
	$: viewerLabel = selectedExcelSheet
		? `Sheet ${selectedExcelSheet}`
		: /\.(pdf|docx|pptx)$/i.test(selectedFile ?? '')
			? `Page ${currentSlide + 1}`
			: '';
	// Preserve translations for the former save notifications during extraction.
	// $i18n.t('File saved'); $i18n.t('Failed to save file');
	const fileSaveController = createFileSaveController({
		getConnection: () => saveConnection,
		isWritable: () => selectedFileWritable,
		uploadFile: uploadToTerminal,
		onSaved: (content) => {
			fileContent = content;
			diskChanged = false;
		},
		nextTick: tick
	});
	const saveFile = fileSaveController.save;

	// Sessions panel state
	let terminalExpanded = false;
	let hasSessions = false;
	let terminalHeight = 200; // px, default when expanded
	let isDraggingHandle = false;
	let containerEl: HTMLElement;
	type ComparisonFile = { name: string; path: string; size?: number };
	let pinnedComparison: ComparisonFile | null = null;
	let comparison: {
		files: [ComparisonFile, ComparisonFile];
		terminal: { url: string; key: string };
		chatId: string | null;
		opener: HTMLElement | null;
	} | null = null;
	const comparisonFile = (entry: FileEntry & { fullPath: string }): ComparisonFile => ({
		name: entry.name,
		path: entry.fullPath,
		size: entry.size
	});
	const openComparison = (files: [ComparisonFile, ComparisonFile]) => {
		if (!selectedTerminal || files[0].path === files[1].path) return;
		comparison = {
			files: files.map((file) => ({ ...file })) as [ComparisonFile, ComparisonFile],
			terminal: { ...selectedTerminal },
			chatId,
			opener: document.activeElement instanceof HTMLElement ? document.activeElement : null
		};
	};
	const closeComparison = async () => {
		const opener = comparison?.opener;
		comparison = null;
		await tick();
		// Let the modal's focus trap finish releasing before restoring the browser control.
		setTimeout(() => (opener?.isConnected ? opener : containerEl)?.focus(), 0);
	};

	const onHandleMouseDown = (e: MouseEvent) => {
		e.preventDefault();
		isDraggingHandle = true;
		const startY = e.clientY;
		const startHeight = terminalHeight;

		const onMouseMove = (ev: MouseEvent) => {
			const delta = startY - ev.clientY;
			const maxH = containerEl ? containerEl.clientHeight - 100 : 500;
			terminalHeight = Math.max(80, Math.min(maxH, startHeight + delta));
		};

		const onMouseUp = () => {
			isDraggingHandle = false;
			window.removeEventListener('mousemove', onMouseMove);
			window.removeEventListener('mouseup', onMouseUp);
		};

		window.addEventListener('mousemove', onMouseMove);
		window.addEventListener('mouseup', onMouseUp);
	};

	// Directory state
	let currentPath = savedPath;
	let fileRoot: TerminalFileRoot | null = savedFileRoot;
	let entries: FileEntry[] = [];
	let currentWritable = true;
	let loading = false;
	let error: string | null = null;

	// Browser view state
	type FileSearchTarget = {
		line: number;
		column: number;
		length: number;
		requestId: number;
	};

	let sortBy: SortMode = 'name';
	let sortAsc = true;
	let showHidden = false;
	let viewMode: 'grid' | 'list' = 'list';
	let visibleEntries: BrowserRow[] = [];
	let expandedDirs: Set<string> = new Set();
	let treeCache: Map<string, FileEntry[]> = new Map();
	let loadingDirs: Set<string> = new Set();
	const fileDirectoryController = createFileDirectoryController({
		lifecycle: fileNavRequestLifecycle,
		getScope: () => ({ terminalId: effectiveTerminalId, chatId }),
		listFiles,
		setCwd,
		onCurrentRequest: (request) => (loadGeneration = request.generation),
		onExpandedLoading: (path, isLoading) => {
			const next = new Set(loadingDirs);
			if (isLoading) next.add(path);
			else next.delete(path);
			loadingDirs = next;
		}
	});
	let directoryMenu: { x: number; y: number } | null = null;
	let searchQuery = '';
	let showSearch = false;
	let searchInput: HTMLInputElement;
	const toggleSearch = async () => {
		showSearch = !showSearch;
		if (!showSearch) searchQuery = '';
		await tick();
		if (showSearch) searchInput?.focus();
		else containerEl?.querySelector<HTMLButtonElement>('[data-file-search-toggle]')?.focus();
	};
	let matchResults: TerminalFileMatch[] | null = null;
	let matchLoading = false;
	let matchLoadingMore = false;
	let matchError: string | null = null;
	let matchLoadMoreError = false;
	let nextMatchOffset: number | null = null;
	let searchTargetRequestId = 0;
	const fileMatchSearchController = createFileMatchSearchController({
		search: (request, offset, signal) =>
			getFileMatches(
				request.baseUrl,
				request.apiKey,
				request.query,
				request.path,
				request.showHidden,
				offset,
				request.sessionId,
				signal
			),
		onState: (state) => {
			matchResults = state.results;
			matchLoading = state.loading;
			matchLoadingMore = state.loadingMore;
			matchError = state.error;
			matchLoadMoreError = state.loadMoreError;
			nextMatchOffset = state.nextOffset;
		},
		errorMessage: () => $i18n.t('Failed to search files')
	});

	$: searchText = searchQuery.trim();
	$: isSearching = Boolean(searchText);
	$: filenameMatches = matchResults?.filter((match) => match.name_match) ?? [];
	$: contentOnlyMatches = matchResults?.filter((match) => !match.name_match) ?? [];

	const toggleSort = (mode: SortMode) => {
		if (sortBy === mode) {
			sortAsc = !sortAsc;
		} else {
			sortBy = mode;
			sortAsc = true;
		}
		void refreshBrowser();
	};

	$: visibleEntries = withRowIndexes(
		buildVisibleRows(
			entries,
			currentPath,
			viewMode === 'list' ? expandedDirs : new Set(),
			treeCache,
			showHidden,
			sortBy,
			sortAsc
		)
	);

	const changeViewMode = (mode: 'grid' | 'list') => {
		clearSelection();
		viewMode = mode;
		localStorage.setItem('fileNav:viewMode', mode);
	};

	// Navigation history
	type NavEntry = { path: string; file: string | null };
	let navHistory: NavEntry[] = [];
	let navIndex = -1;
	let navigatingHistory = false;
	const navigationHistory = createFileNavigationHistory((state) => {
		navHistory = state.entries;
		navIndex = state.index;
	});

	$: canGoBack = navIndex > 0;
	$: canGoForward = navIndex < navHistory.length - 1;

	const pushNavHistory = (path: string, file: string | null = null) => {
		navigationHistory.push(path, file, navigatingHistory);
	};
	const navigateHistoryEntry = (entry: NavEntry) =>
		navigateFileHistoryEntry(entry, {
			setBusy: (busy) => (navigatingHistory = busy),
			loadDirectory: loadDir,
			openFile: async (filePath) => {
				const fileName = filePath.split('/').pop() ?? '';
				await openEntry({ name: fileName, type: 'file', size: 0 });
			}
		});

	const goBack = async () => {
		const entry = navigationHistory.back();
		if (!entry) return;
		await navigateHistoryEntry(entry);
	};

	const goForward = async () => {
		const entry = navigationHistory.forward();
		if (!entry) return;
		await navigateHistoryEntry(entry);
	};

	// File preview state
	let selectedFile: string | null = null;
	let showGitDiff = false;
	let gitFileKey = '';
	$: if (gitFileKey !== `${effectiveTerminalId}:${selectedFile}`) {
		gitFileKey = `${effectiveTerminalId}:${selectedFile}`;
		showGitDiff = false;
	}
	$: currentGitFile =
		gitStatus?.is_repo && gitStatus.root && selectedFile
			? (gitStatus.files.find(
					(file) =>
						normalizePath(`${gitStatus!.root}/${file.path}`) === normalizePath(selectedFile!)
				) ?? null)
			: null;
	let selectedFileWritable = true;
	let fileContent: string | null = null;
	let fileImageUrl: string | null = null;
	let fileVideoUrl: string | null = null;
	let fileAudioUrl: string | null = null;
	let filePdfData: ArrayBuffer | null = null;
	let fileSqliteData: ArrayBuffer | null = null;
	let fileModelData: ArrayBuffer | null = null;
	let fileDocxData: ArrayBuffer | null = null;
	let fileLoading = false;
	let filePreviewRef: FilePreview;
	let commentGeneration = 0;
	let fileSearchTarget: FileSearchTarget | null = null;
	let documentTargetPage: number | null = null;

	// Office preview state
	let fileOfficeHtml: string | null = null;
	let fileOfficeSlides: string[] | null = null;
	let currentSlide = 0;
	let excelSheetNames: string[] = [];
	let selectedExcelSheet = '';
	let excelWorkbook: import('xlsx').WorkBook | null = null;

	// File preview toolbar state (bound from FilePreview)
	let editing = false;
	let showRaw = false;
	let saving = false;

	const MD_EXTS = new Set(['md', 'markdown', 'mdx']);
	const CSV_EXTS = new Set(['csv', 'tsv']);
	const HTML_EXTS = new Set(['html', 'htm']);

	$: isMarkdown = MD_EXTS.has(getFileExt(selectedFile));
	$: isCsv = CSV_EXTS.has(getFileExt(selectedFile));
	$: isHtml = HTML_EXTS.has(getFileExt(selectedFile));
	$: isJson = ['json', 'jsonc', 'jsonl', 'json5'].includes(getFileExt(selectedFile));
	$: isSvg = getFileExt(selectedFile) === 'svg';
	$: isNotebook = getFileExt(selectedFile) === 'ipynb';
	$: isCode = isCodeFile(selectedFile);
	$: isOfficeFile = OFFICE_EXTS.has(getFileExt(selectedFile));
	$: isTextFile =
		fileContent !== null && fileImageUrl === null && filePdfData === null && !isOfficeFile;

	// Upload / folder creation
	let isDragOver = false;
	let uploading = false;
	let creatingFolder = false;
	let newFolderName = '';
	let newFolderInput: HTMLInputElement;
	let creatingFile = false;
	let newFileName = '';
	let newFileInput: HTMLInputElement;
	let directoryUploadInput: HTMLInputElement;

	// Delete confirmation
	let deleteTarget: { path: string; name: string } | null = null;
	let showDeleteConfirm = false;
	let shiftKey = false;

	// Terminal resolution
	let selectedTerminal: { url: string; key: string } | null = null;
	let terminalChatContextPending = false;
	let terminalChatContextHidden = false;
	let isPerChatTerminal = false;
	$: previewBaseUrl =
		selectedTerminal && isPerChatTerminal && chatId
			? `${selectedTerminal.url}/chats/${encodeURIComponent(chatId)}`
			: (selectedTerminal?.url ?? '');

	const chatContext = (terminal: any) => terminal?.contexts?.chat ?? {};

	const getTerminal = (): { url: string; key: string } | null => {
		const systemTerminal = effectiveTerminalId
			? (($terminalServers ?? []).find((t) => t.id === effectiveTerminalId) ?? null)
			: ($terminalServers?.[0] ?? null);
		const chatConfig = chatContext(systemTerminal);
		isPerChatTerminal = !!systemTerminal && chatConfig?.context_id === 'chat_id';
		terminalChatContextHidden =
			!!systemTerminal &&
			(chatConfig === false || (isPerChatTerminal && isTemporaryChatId(chatId)));
		terminalChatContextPending =
			isPerChatTerminal && !terminalChatContextHidden && !isSavedChatId(chatId);
		if (terminalChatContextHidden || terminalChatContextPending) return null;

		const settingsValue: any = $settings;
		const userTerminal = (settingsValue?.terminalServers ?? []).find(
			(s: any) => s.url === effectiveTerminalId
		);

		const isSystem = !!systemTerminal;
		const url = systemTerminal?.url ?? userTerminal?.url ?? '';
		const key = isSystem ? localStorage.token : (userTerminal?.key ?? '');

		return url ? { url, key } : null;
	};

	// Detect terminal or chat changes — the explicit store references ensure
	// Svelte re-runs this block when any of them update.
	// The `mounted` flag prevents the initial run from racing with onMount.
	let prevTerminalUrl = '';
	let prevTerminalId = effectiveTerminalId;
	let prevChatId = chatId;
	let mounted = false;
	let disposed = false;
	$: {
		(effectiveTerminalId, $terminalServers, $settings);
		const terminal = getTerminal();
		selectedTerminal = terminal;

		const chatChanged = chatId !== prevChatId;
		const oldChatId = prevChatId;
		if (chatChanged) prevChatId = chatId;

		const terminalChanged =
			terminal && (terminal.url !== prevTerminalUrl || effectiveTerminalId !== prevTerminalId);
		if (terminalChanged) {
			prevTerminalUrl = terminal.url;
			prevTerminalId = effectiveTerminalId;
		}

		if (chatChanged || terminalChanged || !terminal) {
			comparison = null;
			pinnedComparison = null;
		}

		if (mounted && terminal) {
			if (chatChanged && chatId && !oldChatId) {
				if (openedFile) {
					openedFile = { ...openedFile, session_id: chatId };
					publishFile();
				}
				// Chat just got created (null → real ID): persist the current
				// browsed path as the new session's cwd — don't re-fetch.
				setCwd(terminal.url, terminal.key, savedPath, chatId);
			} else if (terminalChanged || chatChanged) {
				loadGeneration = fileNavRequestLifecycle.invalidate();
				openedFile = null;
				if (terminalContext) terminalContext = { ...terminalContext, file: null };
				// Terminal switched, new chat started, or switched between
				// existing chats — re-fetch the session cwd.
				loading = true;
				error = null;
				entries = [];
				resetTreeState();
				(async () => {
					savedPath = applyCwd(await getCwd(terminal.url, terminal.key, chatId ?? undefined));
					loadDir(savedPath);
				})();
			}
		}
	}

	// Helpers
	const resetTreeState = () => {
		expandedDirs = new Set();
		treeCache = new Map();
		loadingDirs = new Set();
	};

	const saveTreeState = () => {
		treeExpandedCache.set(currentPath, [...expandedDirs]);
		treeContentsCache.set(currentPath, [...treeCache.entries()]);
	};

	const restoreTreeState = (path: string) => {
		expandedDirs = new Set(treeExpandedCache.get(path) ?? []);
		treeCache = new Map(treeContentsCache.get(path) ?? []);
		loadingDirs = new Set();
	};

	const invalidateTreeCache = (...paths: string[]) => {
		const directories = paths.map(asDirectoryPath);
		const next = new Map(treeCache);
		for (const key of next.keys()) {
			if (directories.some((directory) => key === directory || key.startsWith(directory))) {
				next.delete(key);
			}
		}
		treeCache = next;
	};

	const toggleHidden = () => {
		showHidden = !showHidden;
		localStorage.setItem('fileNav:showHidden', String(showHidden));
		clearSelection();
		void refreshBrowser();
	};

	const closeDirectoryMenu = () => {
		directoryMenu = null;
	};

	const parentDirectoryPath = (path: string) => {
		const normalized = normalizePath(path);
		const slash = normalized.lastIndexOf('/');
		return slash > 0 ? asDirectoryPath(normalized.slice(0, slash)) : '/';
	};

	const relativeParentPath = (path: string) => {
		const slash = path.lastIndexOf('/');
		return slash === -1 ? '' : path.slice(0, slash);
	};

	const queueFileSearch = (
		query: string,
		terminal: { url: string; key: string } | null,
		path: string,
		hiddenVisible: boolean,
		activeFile: string | null
	) => {
		if (!query || !terminal || activeFile) {
			fileMatchSearchController.queue(null);
			return;
		}

		clearSelection();
		closeDirectoryMenu();
		creatingFolder = false;
		creatingFile = false;
		fileMatchSearchController.queue({
			baseUrl: terminal.url,
			apiKey: terminal.key,
			query,
			path,
			showHidden: hiddenVisible,
			sessionId: chatId ?? undefined
		});
	};

	$: queueFileSearch(searchText, selectedTerminal, currentPath, showHidden, selectedFile);

	const loadMoreMatches = async () => {
		await fileMatchSearchController.loadMore(
			Boolean(isSearching && !matchLoading && !matchLoadingMore && !matchLoadMoreError)
		);
	};

	const loadMoreOnVisible = (node: HTMLElement) => {
		const observer = new IntersectionObserver(
			([entry]) => {
				if (entry.isIntersecting) void loadMoreMatches();
			},
			{ rootMargin: '160px' }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	};

	const labelFromPath = (path: string) => {
		const parts = normalizePath(path).split('/').filter(Boolean);
		return parts.at(-1) ?? '/';
	};

	const setFileRoot = (root?: TerminalFileRoot) => {
		const previousRoot = fileRoot?.path ?? null;
		fileRoot = root?.path
			? {
					path: asDirectoryPath(root.path),
					label: root.label || labelFromPath(root.path)
				}
			: null;
		savedFileRoot = fileRoot;
		if ((fileRoot?.path ?? null) !== previousRoot) resetTreeState();
	};

	const isInsideFileRoot = (path: string) => {
		if (!fileRoot) return true;
		const directory = asDirectoryPath(path);
		return directory === fileRoot.path || directory.startsWith(fileRoot.path);
	};

	const clampToFileRoot = (path: string) => {
		if (!fileRoot) return asDirectoryPath(path);
		return isInsideFileRoot(path) ? asDirectoryPath(path) : fileRoot.path;
	};

	const rootFromCwd = (cwd: TerminalCwd | null) => {
		const rootPath = cwd?.root?.path ? asDirectoryPath(cwd.root.path) : null;
		return rootPath && rootPath !== '/' ? cwd?.root : undefined;
	};

	const applyCwd = (cwd: TerminalCwd | null) => {
		const cwdPath = cwd?.cwd ? asDirectoryPath(cwd.cwd) : null;
		setFileRoot(rootFromCwd(cwd));
		const path = cwdPath ?? fileRoot?.path ?? '/';
		return clampToFileRoot(path);
	};

	const buildBreadcrumbs = (path: string) => {
		if (fileRoot) {
			const directory = clampToFileRoot(path);
			const relative = directory === fileRoot.path ? '' : directory.slice(fileRoot.path.length);
			const parts = relative.split('/').filter(Boolean);
			return parts.reduce(
				(acc, part) => {
					const prev = acc[acc.length - 1];
					acc.push({ label: part, path: asDirectoryPath(joinPath(prev.path, part)) });
					return acc;
				},
				[{ label: fileRoot.label, path: fileRoot.path }]
			);
		}

		const parts = path.split('/').filter(Boolean);
		const isDrive = /^[A-Za-z]:$/.test(parts[0] ?? '');
		const root = isDrive ? { label: parts[0], path: `${parts[0]}/` } : { label: '/', path: '/' };
		return (isDrive ? parts.slice(1) : parts).reduce(
			(acc, part) => {
				const prev = acc[acc.length - 1];
				acc.push({ label: part, path: asDirectoryPath(joinPath(prev.path, part)) });
				return acc;
			},
			[root]
		);
	};

	// File preview management
	const clearFilePreview = () => {
		commentGeneration++;
		fileSearchTarget = null;
		documentTargetPage = null;
		fileContent = null;
		if (fileImageUrl) {
			URL.revokeObjectURL(fileImageUrl);
			fileImageUrl = null;
		}
		if (fileVideoUrl) {
			URL.revokeObjectURL(fileVideoUrl);
			fileVideoUrl = null;
		}
		if (fileAudioUrl) {
			URL.revokeObjectURL(fileAudioUrl);
			fileAudioUrl = null;
		}
		filePdfData = null;
		fileSqliteData = null;
		fileModelData = null;
		fileDocxData = null;
		fileOfficeHtml = null;
		fileOfficeSlides = null;
		currentSlide = 0;
		excelSheetNames = [];
		selectedExcelSheet = '';
		excelWorkbook = null;
	};

	// Directory operations
	const loadDir = async (
		path: string,
		options: { preserveTree?: boolean; restoreTree?: boolean } = {}
	) => {
		if (disposed) return;
		if (!(await protectBuffer())) return;
		const request = fileDirectoryController.beginCurrentRequest();
		const terminal = selectedTerminal;
		if (!terminal) return;
		const directory = clampToFileRoot(path);
		if (options.restoreTree) {
			restoreTreeState(directory);
		} else if (!options.preserveTree && directory !== currentPath) {
			resetTreeState();
		}

		openedFile = null;
		saveConnection = null;
		dirty = false;
		loading = true;
		error = null;
		selectedFile = null;
		selectedFileWritable = true;
		clearFilePreview();
		clearSelection();
		if (!options.preserveTree || directory !== currentPath || !terminalContext) {
			terminalContext = { terminalId: effectiveTerminalId, currentDirectory: directory };
		}
		currentPath = directory;
		savedPath = directory;
		pushNavHistory(directory);

		const outcome = await fileDirectoryController.loadCurrent(request, {
			terminal,
			path: directory,
			sessionId: chatId ?? undefined,
			initialFile: !!initialFile
		});
		if (outcome.status === 'stale') return;
		loading = false;

		if (outcome.status === 'failed') {
			error =
				'Failed to load directory. Check your Terminal connection in Settings → Integrations.';
			entries = [];
		} else {
			const result = outcome.result;
			currentWritable = result.writable !== false;
			entries = normalizeEntries(result.entries);
			treeCache = new Map(treeCache).set(directory, entries);
			saveTreeState();
		}
		return outcome.status === 'loaded';
	};

	const fetchExpandedDir = async (path: string) => {
		const terminal = selectedTerminal;
		if (!terminal) return null;
		const directory = asDirectoryPath(path);
		const outcome = await fileDirectoryController.loadExpanded(
			directory,
			terminal,
			chatId ?? undefined
		);
		if (outcome.status !== 'loaded') return null;
		const result = outcome.result;
		const normalizedEntries = normalizeEntries(result.entries);
		treeCache = new Map(treeCache).set(directory, normalizedEntries);
		saveTreeState();
		return normalizedEntries;
	};

	const refreshExpandedDirs = async () => {
		for (const directory of [...expandedDirs]) {
			await fetchExpandedDir(directory);
		}
	};

	const refreshBrowser = async () => {
		await loadDir(currentPath, { preserveTree: true });
		await refreshExpandedDirs();
	};

	const toggleExpand = async (path: string) => {
		const directory = asDirectoryPath(path);
		if (expandedDirs.has(directory)) {
			const next = new Set(expandedDirs);
			next.delete(directory);
			expandedDirs = next;
			saveTreeState();
			return;
		}

		expandedDirs = new Set(expandedDirs).add(directory);
		terminalContext = { terminalId: effectiveTerminalId, currentDirectory: directory };
		saveTreeState();

		const result = await fetchExpandedDir(directory);
		if (result === null) {
			const next = new Set(expandedDirs);
			next.delete(directory);
			expandedDirs = next;
			saveTreeState();
			toast.error($i18n.t('Failed to load folder'));
		} else {
			treeCache = new Map(treeCache);
		}
	};

	const openEntry = async (
		entry: FileEntry & Partial<Pick<BrowserRow, 'fullPath' | 'parentPath' | 'depth' | 'rowIndex'>>,
		options: { page?: unknown; refresh?: boolean } = {}
	): Promise<boolean> => {
		const preparation = await prepareFileOpenRequest(entry, options, {
			isDisposed: () => disposed,
			getCurrentPath: () => currentPath,
			getInitialFilePath: () => initialFile?.path,
			hasExternalOpenHandler: () => Boolean(onOpenFile),
			openExternalFile: (path, page) => onOpenFile?.(path, page),
			entryPath,
			normalizePage: normalizeDocumentTargetPage,
			isDirty: () => dirty,
			isSaving: () => saving,
			markDiskChanged: () => (diskChanged = true),
			protectBuffer,
			loadDir
		});
		if (preparation.status !== 'ready') return false;
		const { fullPath: filePath, parentPath } = preparation;
		selectedFileWritable = entry.writable !== false;
		pushNavHistory(parentPath, filePath);

		const terminal = selectedTerminal;
		if (!terminal) return false;

		const selector = effectiveTerminalId!,
			session = chatId;
		const request = fileNavRequestLifecycle.begin(selector, session);
		loadGeneration = request.generation;
		const previousView =
			openedFile?.path === filePath
				? { showRaw, currentSlide, selectedExcelSheet, scroll: viewerRoot?.scrollTop ?? 0 }
				: null;
		const current = () => fileNavRequestLifecycle.isCurrent(request, effectiveTerminalId, chatId);
		openedFile = null;
		if (terminalContext) terminalContext = { ...terminalContext, file: null };
		dirty = false;
		diskChanged = false;
		saveConnection = { ...terminal, path: filePath, chatId: session };
		pendingFile = filesystemFile(
			{ path: filePath, name: entry.name, size: entry.size, modified: entry.modified },
			selector,
			session
		);
		selectedFile = filePath;
		fileLoading = true;
		clearFilePreview();
		documentTargetPage = normalizeDocumentTargetPage(
			options.page ?? (previousView ? previousView.currentSlide + 1 : null)
		);
		if (supportsComments(filePath)) {
			if (previousView) {
				currentSlide = previousView.currentSlide;
				selectedExcelSheet = previousView.selectedExcelSheet;
			}
			fileLoading = false;
			return false;
		}

		const previewResult = await loadCurrentFilePreviewRequest({
			path: filePath,
			terminal,
			sessionId: session,
			translate: (key, options) => $i18n.t(key, options),
			isCurrent: current,
			loadPreview: loadFilePreviewData,
			revokeObjectURL: URL.revokeObjectURL
		});
		if (previewResult.status === 'stale') return false;
		if (previewResult.status === 'failed') {
			fileContent = $i18n.t('Error previewing file: {{error}}', {
				error:
					previewResult.error instanceof Error
						? previewResult.error.message
						: $i18n.t('Unknown error')
			});
			fileLoading = false;
			return false;
		}
		const next = previewResult.preview;
		fileImageUrl = next.fileImageUrl;
		fileVideoUrl = next.fileVideoUrl;
		fileAudioUrl = next.fileAudioUrl;
		filePdfData = next.filePdfData;
		fileSqliteData = next.fileSqliteData;
		fileModelData = next.fileModelData;
		fileDocxData = next.fileDocxData;
		fileOfficeHtml = next.fileOfficeHtml;
		fileOfficeSlides = next.fileOfficeSlides;
		excelWorkbook = next.excelWorkbook;
		excelSheetNames = next.excelSheetNames;
		selectedExcelSheet = next.selectedExcelSheet;
		currentSlide = next.currentSlide;
		fileContent = next.fileContent;
		fileLoading = false;
		const hasPreview =
			next.fileContent !== null ||
			[
				next.fileImageUrl,
				next.fileVideoUrl,
				next.fileAudioUrl,
				next.filePdfData,
				next.fileSqliteData,
				next.fileModelData,
				next.fileDocxData,
				next.fileOfficeHtml,
				next.fileOfficeSlides
			].some(Boolean);
		if (hasPreview) {
			openedFile = pendingFile;

			publishFile();
		}
		if (previousView) {
			showRaw = previousView.showRaw;
			currentSlide = previousView.currentSlide;
			await tick();
			if (viewerRoot) viewerRoot.scrollTop = previousView.scroll;
		}
		return hasPreview;
	};

	const openFileMatch = async (match: TerminalFileMatch) => {
		if (match.type === 'directory') {
			searchQuery = '';
			return await loadDir(match.path);
		}
		return await openEntry({
			name: match.name,
			type: 'file',
			size: 0,
			fullPath: match.path,
			parentPath: parentDirectoryPath(match.path),
			depth: 0,
			rowIndex: -1
		} as BrowserRow);
	};

	const fileContentMatchController = createFileContentMatchController({
		openFileMatch,
		getSearchLength: () => searchText.length,
		nextRequestId: () => ++searchTargetRequestId,
		setSearchTarget: (target) => (fileSearchTarget = target)
	});
	const openContentMatch = fileContentMatchController.openContentMatch;

	let downloading = false;
	const fileDownloadController = createFileDownloadController({
		getTerminal: () => selectedTerminal,
		getSessionId: () => chatId ?? undefined,
		downloadFile: downloadFileBlob,
		archiveDirectory: archiveFromTerminal,
		setDownloading: (value) => (downloading = value),
		showPreparingToast: () => toast.loading($i18n.t('Preparing download...')),
		showDownloadFailed: () => toast.error($i18n.t('Download failed')),
		dismissToast: (id) => toast.dismiss(id),
		createObjectUrl: (blob) => URL.createObjectURL(blob),
		clickDownload: (url, filename) => {
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = filename;
			anchor.click();
		},
		revokeObjectUrl: (url) => URL.revokeObjectURL(url)
	});
	const downloadFile = fileDownloadController.download;

	// Drag-and-drop upload
	const fileUploadController = createFileUploadController({
		uploadFile: uploadToTerminal,
		createDirectory
	});

	const handleDragOver = (e: DragEvent) => {
		if (selectedFile) return;
		if (!currentWritable) return;
		const types = e.dataTransfer?.types;
		if (!types?.includes('Files') && !types?.includes('application/x-terminal-file-move')) return;
		e.preventDefault();
		e.stopPropagation();
		isDragOver = types.includes('Files');
	};

	const handleDrop = async (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
		isDragOver = false;

		const terminal = selectedTerminal;
		if (selectedFile || !terminal || !currentWritable) return;

		const rawMove = e.dataTransfer?.getData('application/x-terminal-file-move');
		if (rawMove) {
			try {
				const data = JSON.parse(rawMove);
				const paths = (data.paths || (data.path ? [data.path] : [])) as string[];
				await handleMovePaths(paths, currentPath);
			} catch {}
			return;
		}

		if (e.dataTransfer && !uploading) await handleUploadEntries(readDroppedFiles(e.dataTransfer));
	};

	const handleUploadEntries = async (
		input: FileUploadEntry[] | Promise<FileUploadEntry[]>
	) => {
		const terminal = selectedTerminal;
		if (!terminal || !currentWritable || uploading) return;
		const destination = currentPath;
		const sessionId = chatId ?? undefined;
		uploading = true;
		try {
			await fileUploadController.upload(await input, terminal, destination, sessionId);
		} catch (error) {
			toast.error(`${$i18n.t('Upload failed')}: ${error instanceof Error ? error.message : error}`);
		} finally {
			uploading = false;
			if (selectedTerminal?.url === terminal.url && (chatId ?? undefined) === sessionId) {
				invalidateTreeCache(destination);
				if (currentPath === destination && !selectedFile) await refreshBrowser();
			}
		}
	};

	const handleUploadFiles = (files: File[]) =>
		handleUploadEntries(
			files.map((file) => ({ path: file.webkitRelativePath || file.name, file }))
		);

	const openUploadPicker = (folder = false) => {
		directoryUploadInput.webkitdirectory = folder;
		directoryUploadInput.click();
	};

	// Folder creation
	const startNewFolderIn = async (path: string) => {
		if (await loadDir(path)) await startNewFolder();
	};

	const startNewFolder = async () => {
		if (!currentWritable) return;
		creatingFolder = true;
		newFolderName = '';
		await tick();
		newFolderInput?.focus();
	};

	const submitNewFolder = async () => {
		const name = newFolderName.trim();
		creatingFolder = false;
		newFolderName = '';
		if (!name) return;

		const terminal = selectedTerminal;
		if (!terminal) return;

		const result = await fileMutationController.createFolder(
			terminal,
			`${currentPath}${name}`,
			chatId ?? undefined
		);
		toast[result ? 'success' : 'error'](
			result ? $i18n.t('Folder created') : $i18n.t('Failed to create folder')
		);
		invalidateTreeCache(currentPath);
		await refreshBrowser();
	};

	// File creation
	const startNewFileIn = async (path: string) => {
		if (await loadDir(path)) await startNewFile();
	};

	const startNewFile = async () => {
		if (!currentWritable) return;
		creatingFile = true;
		newFileName = '';
		await tick();
		newFileInput?.focus();
	};

	const submitNewFile = async () => {
		const name = newFileName.trim();
		creatingFile = false;
		newFileName = '';
		if (!name) return;

		const terminal = selectedTerminal;
		if (!terminal || !currentWritable) return;

		const emptyFile = new File([''], name, { type: 'application/octet-stream' });
		const result = await fileMutationController.createFile(
			terminal,
			currentPath,
			emptyFile,
			chatId ?? undefined
		);
		toast[result ? 'success' : 'error'](
			result ? $i18n.t('File created') : $i18n.t('Failed to create file')
		);
		invalidateTreeCache(currentPath);
		await refreshBrowser();
	};

	// Delete
	const handleDelete = async (path: string, name: string) => {
		const terminal = selectedTerminal;
		if (!terminal || !currentWritable) return;

		const result = await fileMutationController.deletePath(
			terminal,
			path,
			chatId ?? undefined
		);
		toast[result ? 'success' : 'error'](
			result
				? $i18n.t('{{name}} deleted', { name })
				: $i18n.t('Failed to delete {{name}}', { name })
		);
		invalidateTreeCache(currentPath, path);
		await refreshBrowser();
	};

	const requestDelete = (path: string, name: string) => {
		if (!currentWritable) return;
		deleteTarget = { path, name };
		showDeleteConfirm = true;
	};

	// Move (drag-and-drop)
	const fileMutationController = createFileMutationController({
		getTerminal: () => selectedTerminal,
		getSessionId: () => chatId ?? undefined,
		getCurrentPath: () => currentPath,
		canWrite: () => currentWritable,
		createDirectory,
		uploadFile: uploadToTerminal,
		deleteEntry,
		moveEntry
	});

	const sourceParentPath = (source: string) => {
		const cleanSource = normalizePath(source).replace(/\/$/, '');
		const index = cleanSource.lastIndexOf('/');
		return asDirectoryPath(index >= 0 ? cleanSource.slice(0, index + 1) : currentPath);
	};

	// Multi-select
	let selectedEntries: Set<string> = new Set();
	let lastClickedIndex: number | null = null;
	let selectionMode = false;

	$: selectedCount = selectedEntries.size;
	$: comparisonEntries = [...selectedEntries]
		.map((path) => visibleEntries.find((entry) => entry.fullPath === path))
		.filter((entry) => entry !== undefined);
	$: canCompare =
		selectedCount === 2 &&
		comparisonEntries.length === 2 &&
		comparisonEntries.every((entry) => entry.type === 'file');
	$: selectedEntriesWritable =
		currentWritable &&
		[...selectedEntries].every((path) => {
			const entry = visibleEntries.find((item) => item.fullPath === path);
			return entry?.writable !== false;
		});

	const clearSelection = () => {
		selectedEntries = new Set();
		lastClickedIndex = null;
		selectionMode = false;
	};

	const fileMutationWorkflowController = createFileMutationWorkflowController({
		move: (source, destinationFolder) => fileMutationController.move(source, destinationFolder),
		rename: (oldPath, newName) => fileMutationController.rename(oldPath, newName),
		getCurrentPath: () => currentPath,
		sourceParentPath,
		invalidateTreeCache: (...paths) => invalidateTreeCache(...paths),
		clearSelection,
		refreshBrowser,
		onMoveFailed: (error) => toast.error(error),
		onMoved: (name) => toast.success($i18n.t('Moved {{name}}', { name })),
		onRenameFailed: (error) => toast.error(error),
		onRenamed: (name) => toast.success($i18n.t('Renamed to {{name}}', { name }))
	});
	const handleMovePaths = fileMutationWorkflowController.movePaths;
	const handleRename = fileMutationWorkflowController.renamePath;

	const selectAll = () => {
		selectedEntries = selectAllFilePaths(visibleEntries.map((entry) => entry.fullPath));
		selectedEntries = selectedEntries; // trigger reactivity
	};

	const handleSelect = (entry: FileEntry, event: MouseEvent, path?: string, index?: number) => {
		const selectedPath = path ?? entryPath(currentPath, entry);
		const result = toggleFileSelection(
			selectedEntries,
			visibleEntries,
			selectedPath,
			event.shiftKey,
			lastClickedIndex,
			index
		);
		selectedEntries = result.selectedPaths;
		lastClickedIndex = result.lastClickedIndex;
	};

	const enterSelectionMode = () => {
		selectionMode = true;
	};

	const bulkDelete = async () => {
		const terminal = selectedTerminal;
		if (!terminal || !selectedEntriesWritable) return;

		const paths = [...selectedEntries];
		const result = await deleteSelectedFilePaths(paths, (path) =>
			fileMutationController.deletePath(terminal, path, chatId ?? undefined)
		);
		toast[result.deleted > 0 ? 'success' : 'error'](
			$i18n.t('Deleted {{ok}} of {{total}} items', { ok: result.deleted, total: result.total })
		);
		invalidateTreeCache(currentPath, ...paths);
		clearSelection();
		await refreshBrowser();
	};

	const bulkDownload = async () => {
		const terminal = selectedTerminal;
		if (!terminal || downloading) return;

		const plan = createBulkDownloadPlan([...selectedEntries]);
		if (!plan) return;

		// Single file (not dir) — use the regular downloadFile path
		if (plan.kind === 'file') {
			await downloadFile(plan.path);
			return;
		}

		downloading = true;
		const toastId = toast.loading($i18n.t('Preparing download...'));
		try {
			// Archive everything into a single ZIP
			const result = await archiveFromTerminal(
				terminal.url,
				terminal.key,
				plan.paths,
				chatId ?? undefined
			);
			if (!result) {
				toast.error($i18n.t('Download failed'));
				return;
			}
			const url = URL.createObjectURL(result.blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = result.filename;
			a.click();
			URL.revokeObjectURL(url);
		} finally {
			toast.dismiss(toastId);
			downloading = false;
		}
	};

	const selectionEventController = createFileSelectionEventController({
		isComparisonOpen: () => Boolean(comparison),
		getSelectedCount: () => selectedCount,
		clearSelection,
		isActive: () => active,
		isDirectoryMenuOpen: () => Boolean(directoryMenu),
		closeDirectoryMenu: () => (directoryMenu = null),
		getContainer: () => containerEl,
		isModalPath: (path) =>
			path.some((node) => node instanceof Element && node.classList.contains('modal'))
	});
	const handleKeydown = selectionEventController.handleKeydown;
	const handleWindowClick = selectionEventController.handleWindowClick;

	// Lifecycle
	onMount(() => {
		showHidden = localStorage.getItem('fileNav:showHidden') === 'true';
		viewMode = localStorage.getItem('fileNav:viewMode') === 'grid' ? 'grid' : 'list';
		const terminal = getTerminal();

		let handledDisplayFile = false;

		const unsubFileNav = showFileNavPath.subscribe(async (request) => {
			if (!manageFileRequests || !active || !request || !selectedTerminal || comparison) return;
			handledDisplayFile = true;
			showFileNavPath.set(null);
			let filePath = typeof request === 'string' ? request : request.path;
			const targetPage = typeof request === 'string' ? null : request.page;
			filePath = normalizePath(filePath);
			if (!isInsideFileRoot(filePath)) {
				await loadDir(fileRoot?.path ?? '/');
				return;
			}

			const lastSlash = filePath.lastIndexOf('/');
			const dir = lastSlash > 0 ? filePath.substring(0, lastSlash + 1) : '/';
			const fileName = filePath.substring(lastSlash + 1);

			// Always reload directory to ensure entries are fresh
			if (!(await protectBuffer())) return;
			const selector = effectiveTerminalId,
				session = chatId;
			await loadDir(dir);
			if (selector !== effectiveTerminalId || session !== chatId || currentPath !== dir || dirty)
				return;
			await tick();

			const entry = entries.find((e) => e.name === fileName);
			if (entry) {
				await openEntry(entry, { page: targetPage });
			} else {
				// File may not be in listing; open it directly
				await openEntry({ name: fileName, type: 'file', size: 0 }, { page: targetPage });
			}
		});

		const unsubFileNavDir = showFileNavDir.subscribe(async (filePath) => {
			if (!filePath || !selectedTerminal || effectiveTerminalId !== $selectedTerminalId) return;
			showFileNavDir.set(null);
			filePath = normalizePath(filePath);
			if (!isInsideFileRoot(filePath)) {
				filePath = fileRoot?.path ?? '/';
			}

			const lastSlash = filePath.lastIndexOf('/');
			const dir = lastSlash > 0 ? filePath.substring(0, lastSlash + 1) : '/';
			invalidateTreeCache(dir);
			if (comparison) return;

			if (selectedFile) {
				if (selectedFile === filePath) {
					const fileName = selectedFile.split('/').pop() ?? '';
					await openEntry({ name: fileName, type: 'file', size: 0 }, { refresh: true });
				}
			} else {
				if (currentPath.startsWith(dir) || dir.startsWith(currentPath)) {
					await refreshBrowser();
				}
			}
		});

		if (!handledDisplayFile && terminal) {
			loading = true;

			void (async () => {
				const serverCwd = await getCwd(terminal.url, terminal.key, chatId ?? undefined);
				if (disposed) return;
				const serverPath = applyCwd(serverCwd);
				savedPath =
					terminalContext && terminalContext.terminalId === effectiveTerminalId
						? terminalContext.currentDirectory
						: serverPath;
				savedPath = clampToFileRoot(savedPath);
				if (initialFile) {
					const path = normalizePath(initialFile.path);
					const slash = path.lastIndexOf('/');
					await loadDir(slash > 0 ? path.slice(0, slash + 1) : '/');
					await openEntry(
						entries.find((entry) => entry.name === path.slice(slash + 1)) ?? {
							name: path.slice(slash + 1),
							type: 'file',
							writable: currentWritable
						},
						{ page: initialFile.page }
					);
				} else {
					await loadDir(initialDirectory ?? savedPath, { restoreTree: true });
				}
			})();
		}

		mounted = true;

		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key === 'Shift') shiftKey = true;
		};
		const onKeyUp = (e: KeyboardEvent) => {
			if (e.key === 'Shift') shiftKey = false;
		};
		const onBlur = () => (shiftKey = false);

		const onVisibilityChange = () => {
			if (
				active &&
				document.visibilityState === 'visible' &&
				!comparison &&
				!selectedFile &&
				selectedTerminal &&
				!terminalChatContextPending &&
				!loading
			) {
				loadDir(currentPath, { preserveTree: true });
			}
		};

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);
		window.addEventListener('blur', onBlur);
		document.addEventListener('visibilitychange', onVisibilityChange);

		return () => {
			unsubFileNav();
			unsubFileNavDir();
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);
			window.removeEventListener('blur', onBlur);
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

	onDestroy(() => {
		disposed = true;
		loadGeneration = fileNavRequestLifecycle.invalidate();
		dirtyChoice?.resolve('cancel');
		fileMatchSearchController.clearRequest();
		if (fileImageUrl) URL.revokeObjectURL(fileImageUrl);
		if (fileVideoUrl) URL.revokeObjectURL(fileVideoUrl);
		if (fileAudioUrl) URL.revokeObjectURL(fileAudioUrl);
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	on:confirm={() => {
		if (deleteTarget) {
			if (deleteTarget.path === '__bulk__') {
				bulkDelete();
			} else {
				handleDelete(deleteTarget.path, deleteTarget.name);
			}
			deleteTarget = null;
		}
	}}
/>

<svelte:document
	on:selectionchange={captureSelection}
	on:focusin={(event) => {
		usingFileAsk = !!(event.target as Element)?.closest('[data-file-ask]');
	}}
/>

<svelte:window
	on:keydown={(event) => {
		if (!active) return;
		if (event.key === 'Escape' && fileAsk) {
			fileAsk = null;
			event.preventDefault();
		} else handleKeydown(event);
	}}
	on:click={handleWindowClick}
	on:pointerdown|capture={(event) => {
		usingFileAsk = !!(event.target as Element)?.closest('[data-file-ask]');
		if (fileAsk && !dirtyChoice && !usingFileAsk) fileAsk = null;
	}}
/>

{#if comparison}
	{#key comparison}
		<FileComparisonModal
			files={comparison.files}
			baseUrl={comparison.terminal.url}
			apiKey={comparison.terminal.key}
			chatId={comparison.chatId}
			onClose={closeComparison}
		/>
	{/key}
{/if}

{#if terminalChatContextHidden}
	<div class="hidden"></div>
{:else if terminalChatContextPending}
	<div class="flex-1 flex flex-col items-center justify-center p-6 text-center">
		<Icon
			name="terminal"
			size={24}
			strokeWidth={1.4}
			class="text-gray-300 dark:text-gray-600 mb-2"
		/>
		<div class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t('Start the chat to use this terminal.')}
		</div>
	</div>
{:else if !selectedTerminal}
	<div class="flex-1 flex flex-col items-center justify-center p-6 text-center">
		<Icon
			name="terminal"
			size={24}
			strokeWidth={1.4}
			class="text-gray-300 dark:text-gray-600 mb-2"
		/>
		<div class="text-xs text-gray-500 dark:text-gray-400 mb-1">
			{$i18n.t('No Terminal connection configured.')}
		</div>
		<div class="text-[0.625rem] text-gray-400 dark:text-gray-500">
			{$i18n.t('Add your Open Terminal URL and API key in Settings → Integrations.')}
		</div>
	</div>
{:else}
	<div
		bind:this={containerEl}
		class="flex flex-col h-full min-h-0 min-w-0 relative"
		on:dragover={(e) => !isSearching && handleDragOver(e)}
		on:dragleave={(e) => {
			if (!(e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget))) {
				isDragOver = false;
			}
		}}
		on:drop={(e) => !isSearching && handleDrop(e)}
		role="region"
		aria-label={$i18n.t('File browser')}
		tabindex="-1"
	>
		{#if isDragOver && !isSearching}
			<div
				class="pointer-events-none absolute inset-0 z-10 bg-black/[0.02] dark:bg-white/[0.02]"
				role="status"
			>
				<div
					class="absolute left-1/2 top-1/2 flex max-w-[calc(100%-1rem)] -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-lg bg-white/95 px-2.5 py-1.5 text-xs text-gray-600 shadow-sm ring-1 ring-black/5 dark:bg-gray-850/95 dark:text-gray-300 dark:ring-white/10"
				>
					<Icon name="upload-tray" size={14} strokeWidth={1.5} />
					<span class="truncate">{$i18n.t('Drop to upload')}</span>
				</div>
			</div>
		{/if}

		<FileNavToolbar
			gitFile={currentGitFile}
			gitDiffOpen={showGitDiff}
			onToggleGitDiff={() => {
				showGitDiff = !showGitDiff;
				fileAsk = null;
			}}
			breadcrumbs={buildBreadcrumbs(currentPath)}
			{selectedFile}
			fileSize={openedFile?.size ?? pendingFile?.size}
			{loading}
			{showSearch}
			onToggleSearch={toggleSearch}
			writable={currentWritable}
			canGoBack={!initialFile && canGoBack}
			canGoForward={!initialFile && canGoForward}
			{sortBy}
			{sortAsc}
			showDateAdded
			{showHidden}
			viewMode={isSearching ? null : viewMode}
			onViewModeChange={changeViewMode}
			onGoBack={goBack}
			onGoForward={goForward}
			onNavigate={initialFile && onBrowseDirectory ? onBrowseDirectory : loadDir}
			onRefresh={() => {
				if (selectedFile) {
					const fileName = selectedFile.split('/').pop() ?? '';
					openEntry({ name: fileName, type: 'file', size: 0 });
				} else {
					refreshBrowser();
				}
			}}
			onNewFolder={startNewFolder}
			onNewFile={startNewFile}
			onUploadFiles={handleUploadFiles}
			onUploadFolder={() => openUploadPicker(true)}
			onDownloadDir={() => downloadFile(currentPath)}
			onMove={handleMovePaths}
			onSort={toggleSort}
			onToggleHidden={toggleHidden}
		>
			{#if (isMarkdown || isCsv || isHtml || isJson || isSvg || isNotebook) && fileContent !== null && !editing}
				<Tooltip content={showRaw ? $i18n.t('Preview') : $i18n.t('Source')}>
					<button
						class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
						on:click={async () => {
							if (!(await protectBuffer())) return;
							if (editing) filePreviewRef?.cancelEdit();
							showRaw = !showRaw;
						}}
						aria-label={showRaw ? $i18n.t('Preview') : $i18n.t('Source')}
					>
						{#if showRaw}
							<Icon name="eye" size={11} strokeWidth={1.4} />
						{:else}
							<Icon name="code" size={11} strokeWidth={1.4} />
						{/if}
					</button>
				</Tooltip>
			{/if}
			{#if fileContent !== null}
				<Tooltip content={$i18n.t('Copy')}>
					<button
						class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
						on:click={async () => {
							if (await copyToClipboard(fileContent ?? '')) {
								toast.success($i18n.t('Copied to clipboard'));
							}
						}}
						aria-label={$i18n.t('Copy')}
					>
						<Icon name="copy" size={11} strokeWidth={1.4} />
					</button>
				</Tooltip>
			{/if}
			<Tooltip content={$i18n.t('Download')}>
				<button
					class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
					on:click={() => selectedFile && downloadFile(selectedFile)}
					aria-label={$i18n.t('Download')}
				>
					<Icon name="download" size={11} strokeWidth={1.4} />
				</button>
			</Tooltip>

			{#if isTextFile}
				{#if isHtml && showRaw}
					<Tooltip content={$i18n.t('Save')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-30 disabled:hover:bg-transparent"
							on:click={() =>
								filePreviewRef?.saveCodeFile().catch((error) => toast.error(String(error)))}
							disabled={saving || !selectedFileWritable}
							aria-label={$i18n.t('Save')}
						>
							{#if saving}
								<Spinner className="size-3.5" />
							{:else}
								<Icon name="check" size={11} strokeWidth={1.7} />
							{/if}
						</button>
					</Tooltip>
				{:else if isHtml}
					<!-- HTML preview mode: no edit/save buttons -->
				{:else if isMarkdown && showRaw}
					<Tooltip content={$i18n.t('Save')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-30 disabled:hover:bg-transparent"
							on:click={() =>
								filePreviewRef?.saveCodeFile().catch((error) => toast.error(String(error)))}
							disabled={saving || !selectedFileWritable}
							aria-label={$i18n.t('Save')}
						>
							{#if saving}
								<Spinner className="size-3.5" />
							{:else}
								<Icon name="check" size={11} strokeWidth={1.7} />
							{/if}
						</button>
					</Tooltip>
				{:else if isMarkdown}
					<!-- Markdown preview mode: no edit/save buttons -->
				{:else if isCode}
					<Tooltip content={$i18n.t('Save')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-30 disabled:hover:bg-transparent"
							on:click={() =>
								filePreviewRef?.saveCodeFile().catch((error) => toast.error(String(error)))}
							disabled={saving || !selectedFileWritable}
							aria-label={$i18n.t('Save')}
						>
							{#if saving}
								<Spinner className="size-3.5" />
							{:else}
								<Icon name="check" size={11} strokeWidth={1.7} />
							{/if}
						</button>
					</Tooltip>
				{:else if editing}
					<Tooltip content={$i18n.t('Cancel')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
							on:click={() => filePreviewRef?.cancelEdit()}
							aria-label={$i18n.t('Cancel')}
						>
							<Icon name="xmark" size={11} strokeWidth={1.7} />
						</button>
					</Tooltip>
					<Tooltip content={$i18n.t('Save')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-30 disabled:hover:bg-transparent"
							on:click={() =>
								filePreviewRef?.saveEdit().catch((error) => toast.error(String(error)))}
							disabled={saving || !selectedFileWritable}
							aria-label={$i18n.t('Save')}
						>
							{#if saving}
								<Spinner className="size-3.5" />
							{:else}
								<Icon name="check" size={11} strokeWidth={1.7} />
							{/if}
						</button>
					</Tooltip>
				{:else}
					<Tooltip content={$i18n.t('Edit')}>
						<button
							class="shrink-0 flex h-5 w-5 items-center justify-center rounded transition-colors duration-100 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-30 disabled:hover:bg-transparent"
							on:click={() => filePreviewRef?.startEdit()}
							disabled={!selectedFileWritable}
							aria-label={$i18n.t('Edit')}
						>
							<Icon name="pencil" size={11} strokeWidth={1.4} />
						</button>
					</Tooltip>
				{/if}
			{/if}
		</FileNavToolbar>

		{#if !selectedFile && showSearch}
			<div
				class="flex h-8 shrink-0 items-center gap-1.5 border-b border-gray-50 px-3 dark:border-gray-850/30"
			>
				<Icon name="search" size={13} strokeWidth={1.5} class="shrink-0 text-gray-400" />
				<input
					bind:this={searchInput}
					type="text"
					class="min-w-0 flex-1 border-none bg-transparent text-xs text-gray-800 outline-none placeholder:text-gray-400 dark:text-gray-200 dark:placeholder:text-gray-600"
					placeholder={$i18n.t('Search files and contents')}
					aria-label={$i18n.t('Search files and contents')}
					bind:value={searchQuery}
					on:keydown={(event) => {
						if (event.key === 'Escape') {
							event.preventDefault();
							event.stopPropagation();
							void toggleSearch();
						}
					}}
				/>
				{#if searchQuery}
					<button
						class="flex shrink-0 items-center text-gray-400 transition hover:text-gray-600 dark:hover:text-gray-300"
						on:click={() => (searchQuery = '')}
						aria-label={$i18n.t('Clear search')}
					>
						<Icon name="xmark" size={11} strokeWidth={1.5} />
					</button>
				{/if}
			</div>
		{/if}

		{#if pinnedComparison}
			<div class="flex shrink-0 items-center gap-2 px-3 py-1.5 text-xs text-gray-500">
				<Tooltip content={pinnedComparison.path}>
					<span class="block max-w-[16rem] truncate"
						>{$i18n.t('Comparing with {{name}}', { name: pinnedComparison.name })}</span
					>
				</Tooltip>
				<button
					type="button"
					class="shrink-0 hover:text-gray-900 dark:hover:text-white"
					aria-label={$i18n.t('Clear comparison selection')}
					on:click={() => (pinnedComparison = null)}><Icon name="xmark" size={12} /></button
				>
			</div>
		{/if}

		<!-- Bulk action bar -->
		{#if selectedCount > 0 && !isSearching}
			<BulkActionBar
				count={selectedCount}
				{canCompare}
				onCompare={() => {
					if (canCompare)
						openComparison([
							comparisonFile(comparisonEntries[0]),
							comparisonFile(comparisonEntries[1])
						]);
				}}
				canDelete={selectedEntriesWritable}
				onDelete={() => {
					deleteTarget = { path: '__bulk__', name: `${selectedCount} items` };
					showDeleteConfirm = true;
				}}
				onDownload={bulkDownload}
				onSelectAll={selectAll}
				onClear={clearSelection}
			/>
		{/if}

		{#if diskChanged}<div
				role="status"
				class="flex flex-wrap items-center gap-2 px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400"
			>
				{$i18n.t('File updated on disk. Your local changes are preserved.')}
				<button
					class="underline"
					on:click={() => openEntry({ name: selectedFile!.split('/').pop()!, type: 'file' })}
					>{$i18n.t('Reload')}</button
				>
				<button
					class="underline"
					on:click={() => filePreviewRef.save().catch((e) => toast.error(String(e)))}
					>{$i18n.t('Save local changes')}</button
				>
			</div>{/if}
		<!-- Content -->
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div
			bind:this={viewerRoot}
			class="flex-1 overflow-y-auto min-h-0 min-w-0"
			on:click={(e) => {
				closeDirectoryMenu();
				if (!comparison && e.target === e.currentTarget && selectedCount > 0) clearSelection();
			}}
			on:contextmenu={(e) => {
				if (comparison || selectedFile || isSearching) return;
				if ((e.target as HTMLElement)?.closest('[data-file-row]')) return;
				e.preventDefault();
				directoryMenu = { x: e.clientX, y: e.clientY };
			}}
		>
			{#if selectedFile !== null}
				{#if showGitDiff}
					<FileGitDiff
						connection={gitConnection}
						{chatId}
						root={gitStatus?.root ?? currentPath}
						file={currentGitFile}
						active={active && !fileLoading}
						mode={gitDiffMode}
						hideWhitespace={gitHideWhitespace}
						resourceVersion={commentGeneration}
					/>
				{/if}
				<div class="h-full" class:hidden={showGitDiff}>
					{#if supportsComments(selectedFile) && selectedTerminal}
						{#key `${selectedTerminal.url}|${selectedTerminal.key}|${chatId}|${selectedFile}|${commentGeneration}`}
							<CommentFilePreview
								path={selectedFile}
								baseUrl={selectedTerminal.url}
								apiKey={selectedTerminal.key}
								{chatId}
								targetPage={documentTargetPage}
								initialSheet={selectedExcelSheet}
								onReady={() => {
									openedFile = pendingFile;
									publishFile();
								}}
								bind:currentSlide
								bind:sheet={selectedExcelSheet}
							/>
						{/key}
					{:else}
						<FilePreview
							bind:this={filePreviewRef}
							bind:editing
							bind:showRaw
							bind:saving
							bind:dirty
							{chatId}
							resourceVersion={commentGeneration}
							onLocalLink={(path) =>
								onOpenFile ? onOpenFile(path) : showFileNavPath.set({ path })}
							bind:currentSlide
							{selectedFile}
							readOnly={!selectedFileWritable}
							{fileLoading}
							{fileImageUrl}
							{fileVideoUrl}
							{fileAudioUrl}
							{filePdfData}
							{fileSqliteData}
							{fileModelData}
							{fileDocxData}
							{fileContent}
							{fileOfficeHtml}
							{fileOfficeSlides}
							targetPage={documentTargetPage}
							{excelSheetNames}
							{selectedExcelSheet}
							searchTarget={fileSearchTarget}
							onSheetChange={async (sheet) => {
								if (!excelWorkbook) return;
								selectedExcelSheet = sheet;
								const { excelToTable } = await import('$lib/utils/excelToTable');
								const result = await excelToTable(excelWorkbook.Sheets[sheet]);
								const DOMPurify = (await import('dompurify')).default;
								fileOfficeHtml = DOMPurify.sanitize(result.html);
							}}
							baseUrl={previewBaseUrl}
							apiKey={selectedTerminal?.key ?? ''}
							overlay={overlay || isDraggingHandle}
							onSave={saveFile}
						/>
					{/if}
				</div>
			{:else}
				{#if isSearching}
					{#if matchLoading}
						<div class="flex justify-center pt-8"><Spinner className="size-4" /></div>
					{:else if matchError}
						<div class="flex items-center justify-center py-12">
							<div class="text-xs text-gray-400 dark:text-gray-500">{matchError}</div>
						</div>
					{:else if !matchResults?.length}
						<div class="flex items-center justify-center py-12">
							<div class="text-xs text-gray-400 dark:text-gray-500">{$i18n.t('No matches')}</div>
						</div>
					{:else}
						{#if filenameMatches.length > 0}
							<div
								class="px-2 pt-1 pb-0.5 text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600"
							>
								{$i18n.t('Filename matches')}
							</div>
							{#each filenameMatches as match (match.path)}
								<button
									class="flex h-7 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors duration-75 hover:bg-gray-50 dark:hover:bg-white/4"
									on:click={() => openFileMatch(match)}
								>
									<FileTypeIcon name={match.name} type={match.type} />
									<span class="min-w-0 flex-1 truncate text-xs text-gray-800 dark:text-gray-200">
										{match.name}
										{#if relativeParentPath(match.relative_path)}
											<span class="ml-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
												{relativeParentPath(match.relative_path)}
											</span>
										{/if}
									</span>
								</button>
								{#if match.content_matches.length > 0}
									{@const preview = match.content_matches[0]}
									<button
										class="flex h-6 w-full items-center gap-2 rounded-lg pl-8 pr-2 text-left transition-colors duration-75 hover:bg-gray-50 dark:hover:bg-white/4"
										on:click={() => openContentMatch(match, preview)}
									>
										<span
											class="w-6 shrink-0 text-right font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
											>{preview.line}</span
										>
										<span
											class="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-gray-500 dark:text-gray-500"
											>{preview.text}</span
										>
										{#if match.content_matches.length > 1}
											<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600">
												+{match.content_matches.length - 1}
											</span>
										{/if}
									</button>
								{/if}
							{/each}
						{/if}

						{#if contentOnlyMatches.length > 0}
							<div
								class="px-2 pt-2 pb-0.5 text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600"
							>
								{$i18n.t('Content matches')}
							</div>
							{#each contentOnlyMatches as match (match.path)}
								<button
									class="flex h-7 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors duration-75 hover:bg-gray-50 dark:hover:bg-white/4"
									on:click={() => openFileMatch(match)}
								>
									<FileTypeIcon name={match.name} type={match.type} />
									<span class="min-w-0 flex-1 truncate text-xs text-gray-800 dark:text-gray-200">
										{match.name}
										{#if relativeParentPath(match.relative_path)}
											<span class="ml-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
												{relativeParentPath(match.relative_path)}
											</span>
										{/if}
									</span>
								</button>
								{#if match.content_matches.length > 0}
									{@const preview = match.content_matches[0]}
									<button
										class="flex h-6 w-full items-center gap-2 rounded-lg pl-8 pr-2 text-left transition-colors duration-75 hover:bg-gray-50 dark:hover:bg-white/4"
										on:click={() => openContentMatch(match, preview)}
									>
										<span
											class="w-6 shrink-0 text-right font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
											>{preview.line}</span
										>
										<span
											class="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-gray-500 dark:text-gray-500"
											>{preview.text}</span
										>
										{#if match.content_matches.length > 1}
											<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600">
												+{match.content_matches.length - 1}
											</span>
										{/if}
									</button>
								{/if}
							{/each}
						{/if}

						{#if nextMatchOffset !== null}
							<div use:loadMoreOnVisible class="flex h-8 items-center justify-center">
								{#if matchLoadingMore}
									<Spinner className="size-3" />
								{:else if matchLoadMoreError}
									<button
										class="text-[0.6875rem] text-gray-400 transition-colors duration-75 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-400"
										on:click={() => {
											matchLoadMoreError = false;
											void loadMoreMatches();
										}}>{$i18n.t('Retry')}</button
									>
								{/if}
							</div>
						{/if}
					{/if}
				{:else if uploading}
					<div class="flex items-center justify-center gap-2 p-4 text-xs text-gray-500">
						<Spinner className="size-4" />
						{$i18n.t('Uploading...')}
					</div>
				{:else if loading || (effectiveTerminalId && $terminalServers === null)}
					<div class="flex justify-center pt-8"><Spinner className="size-4" /></div>
				{:else if error}
					<div class="p-4 text-xs">{error}</div>
				{:else if visibleEntries.length === 0 && !creatingFolder && !creatingFile}
					<div class="flex items-center justify-center py-12">
						<div class="text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('This folder is empty')}
						</div>
					</div>
				{/if}

				{#if !isSearching && !loading && !error && !uploading && !(effectiveTerminalId && $terminalServers === null)}
					{#if visibleEntries.length > 0 || creatingFolder || creatingFile}
						<ul
							class={viewMode === 'grid'
								? 'grid grid-cols-[repeat(auto-fill,minmax(min(94px,calc((100%_-_0.5rem)/2)),1fr))] gap-2 p-2.5'
								: ''}
						>
							{#if creatingFolder}
								<li
									class={viewMode === 'grid'
										? 'order-last relative flex h-17 min-w-0 flex-col items-start self-start px-1.5 pt-4 pb-1.5'
										: 'flex h-7 items-center gap-1.5 pl-[26px] pr-2'}
								>
									{#if viewMode === 'grid'}
										<svg
											class="pointer-events-none absolute inset-0 h-full w-full text-black/10 dark:text-white/10"
											viewBox="0 0 200 68"
											preserveAspectRatio="none"
											fill="none"
											stroke="currentColor"
											stroke-width="1"
											aria-hidden="true"
										>
											<path
												d="M1 19V8Q1 1 9 1H54Q58 1 61 4L65 7Q67 9 71 9H191Q199 9 199 15V19"
												vector-effect="non-scaling-stroke"
											/>
											<path
												d="M1 20Q1 13 9 13H191Q199 13 199 20V60Q199 67 191 67H9Q1 67 1 60Z"
												vector-effect="non-scaling-stroke"
											/>
										</svg>
									{:else}
										<FileTypeIcon name={newFolderName} type="directory" />
									{/if}
									<input
										bind:this={newFolderInput}
										bind:value={newFolderName}
										class={viewMode === 'grid'
											? 'relative w-full min-w-0 bg-transparent py-0.5 text-xs text-gray-800 outline-hidden dark:text-gray-200'
											: 'min-w-0 flex-1 bg-transparent py-0.5 text-xs outline-hidden'}
										placeholder={$i18n.t('Folder name')}
										aria-label={$i18n.t('Folder name')}
										on:keydown={(e) => {
											if (e.key === 'Enter') submitNewFolder();
											if (e.key === 'Escape') {
												creatingFolder = false;
												newFolderName = '';
											}
										}}
										on:blur={submitNewFolder}
									/>
								</li>
							{/if}
							{#if creatingFile}
								<li
									class={viewMode === 'grid'
										? 'order-last flex h-[3.875rem] min-w-0 flex-col items-start self-start rounded-md border border-black/[0.06] bg-gray-50/40 p-1.5 dark:border-white/[0.06] dark:bg-gray-900'
										: 'flex h-7 items-center gap-1.5 pl-[26px] pr-2'}
								>
									{#if viewMode === 'list'}
										<FileTypeIcon name={newFileName} type="file" />
									{/if}
									<input
										bind:this={newFileInput}
										bind:value={newFileName}
										class={viewMode === 'grid'
											? 'relative w-full min-w-0 bg-transparent py-0.5 text-xs text-gray-800 outline-hidden dark:text-gray-200'
											: 'min-w-0 flex-1 bg-transparent py-0.5 text-xs outline-hidden'}
										placeholder={$i18n.t('File name')}
										aria-label={$i18n.t('File name')}
										on:keydown={(e) => {
											if (e.key === 'Enter') submitNewFile();
											if (e.key === 'Escape') {
												creatingFile = false;
												newFileName = '';
											}
										}}
										on:blur={submitNewFile}
									/>
								</li>
							{/if}
							{#each visibleEntries as entry (entry.fullPath)}
								<FileEntryRow
									{entry}
									{viewMode}
									currentPath={entry.parentPath}
									fullPath={entry.fullPath}
									depth={entry.depth}
									rowIndex={entry.rowIndex}
									expanded={expandedDirs.has(entry.fullPath)}
									loadingChildren={loadingDirs.has(entry.fullPath)}
									folderContentsVersion={loadGeneration}
									terminalUrl={selectedTerminal.url}
									terminalKey={selectedTerminal.key}
									terminalSessionId={chatId ?? undefined}
									selected={selectedEntries.has(entry.fullPath)}
									{selectionMode}
									selectedPaths={selectedEntries}
									onOpen={(row) =>
										openEntry({
											...row,
											fullPath: entry.fullPath,
											parentPath: entry.parentPath,
											depth: entry.depth,
											rowIndex: entry.rowIndex
										})}
									onSelectForComparison={() => (pinnedComparison = comparisonFile(entry))}
									comparisonPath={pinnedComparison?.path ?? null}
									onCompareWithSelected={pinnedComparison
										? () => {
												if (pinnedComparison)
													openComparison([pinnedComparison, comparisonFile(entry)]);
											}
										: undefined}
									onDownload={downloadFile}
									onNewFolder={startNewFolderIn}
									onNewFile={startNewFileIn}
									onDelete={requestDelete}
									onMove={handleMovePaths}
									onRename={handleRename}
									onSelect={handleSelect}
									onLongPress={enterSelectionMode}
									onToggleExpand={toggleExpand}
									showDate={sortBy === 'date' || sortBy === 'created'}
									dateField={sortBy === 'created' ? 'created' : 'modified'}
									{showHidden}
									parentWritable={currentWritable}
								/>
							{/each}
						</ul>
					{/if}
				{/if}
			{/if}
		</div>

		<!-- Read-only session output -->
		{#if showTerminalDock}
			<div
				class:hidden={!hasSessions}
				class="shrink-0 border-t border-gray-50 dark:border-gray-850/30"
			>
				{#if terminalExpanded}
					<!-- Drag handle (at top of panel) -->
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div class="relative cursor-row-resize group" on:mousedown={onHandleMouseDown}>
						<div
							class="h-px bg-transparent group-hover:bg-black/10 dark:group-hover:bg-white/10 transition"
						></div>
						<div class="absolute inset-x-0 -top-1.5 -bottom-1.5"></div>
					</div>
				{/if}

				{#key JSON.stringify([chatId, effectiveTerminalId])}
					<TerminalDock
						terminalId={effectiveTerminalId}
						{active}
						bind:hasSessions
						overlay={overlay || isDraggingHandle}
						bind:expanded={terminalExpanded}
						height={terminalHeight}
						{chatId}
					/>
				{/key}
			</div>
		{/if}

		<input
			bind:this={directoryUploadInput}
			type="file"
			multiple
			hidden
			on:change={async () => {
				if (!currentWritable || !directoryUploadInput?.files?.length) return;
				await handleUploadFiles(Array.from(directoryUploadInput.files));
				directoryUploadInput.value = '';
			}}
		/>

		{#if directoryMenu}
			<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
			<div
				class="fixed z-[9999999]"
				style="left: {directoryMenu.x}px; top: {directoryMenu.y}px;"
				on:click|stopPropagation
			>
				<DropdownMenu className="min-w-[9.375rem]">
					<button
						type="button"
						class="select-none flex h-7 w-full items-center gap-2 rounded-lg px-2 text-xs hover:bg-gray-50/40 dark:hover:bg-white/4 transition"
						on:click={() => {
							closeDirectoryMenu();
							refreshBrowser();
						}}
					>
						<Icon name="refresh" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('Refresh')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-40"
						disabled={!currentWritable}
						on:click={() => {
							closeDirectoryMenu();
							startNewFile();
						}}
					>
						<Icon name="file-plus" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('New File')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-40"
						disabled={!currentWritable}
						on:click={() => {
							closeDirectoryMenu();
							startNewFolder();
						}}
					>
						<Icon name="folder-plus" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('New Folder')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-7 w-full items-center gap-2 rounded-lg px-2 text-xs hover:bg-gray-50/40 dark:hover:bg-white/4 transition disabled:opacity-40 disabled:hover:bg-transparent"
						disabled={!currentWritable}
						on:click={() => {
							closeDirectoryMenu();
							openUploadPicker();
						}}
					>
						<Icon name="upload-tray" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('Upload')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-7 w-full items-center gap-2 rounded-lg px-2 text-xs hover:bg-gray-50/40 dark:hover:bg-white/4 transition disabled:opacity-40 disabled:hover:bg-transparent"
						disabled={!currentWritable}
						on:click={() => {
							closeDirectoryMenu();
							openUploadPicker(true);
						}}
					>
						<Icon name="folder-upload" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('Upload Folder')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-7 w-full items-center gap-2 rounded-lg px-2 text-xs hover:bg-gray-50/40 dark:hover:bg-white/4 transition"
						on:click={() => {
							closeDirectoryMenu();
							downloadFile(currentPath);
						}}
					>
						<Icon name="download-tray" size={14} strokeWidth={1.5} />
						<span>{$i18n.t('Download')}</span>
					</button>
					<button
						type="button"
						class="select-none flex h-7 w-full items-center gap-2 rounded-lg px-2 text-xs hover:bg-gray-50/40 dark:hover:bg-white/4 transition"
						on:click={() => {
							closeDirectoryMenu();
							toggleHidden();
						}}
					>
						<Icon name={showHidden ? 'eye-off' : 'eye'} size={14} strokeWidth={1.5} />
						<span>{showHidden ? $i18n.t('Hide Hidden Files') : $i18n.t('Show Hidden Files')}</span>
					</button>
				</DropdownMenu>
			</div>
		{/if}
	</div>
{/if}

{#if fileAsk}
	{#key fileAsk.anchor}
		<form
			use:positionFileAsk={fileAsk.anchor}
			data-file-ask
			class="fixed z-50 w-80 max-w-[calc(100vw-1.5rem)] rounded-2xl bg-white pt-3 pl-3 pr-2 pb-2 shadow-[0_16px_40px_-28px_rgba(0,0,0,0.55)] ring-1 ring-black/5 dark:bg-gray-850 dark:text-white dark:ring-white/10"
			aria-label={$i18n.t('Ask about this file')}
			on:submit|preventDefault={submitFileAsk}
		>
			{#if fileAsk.text}
				<blockquote
					class="mb-2 border-l-2 border-gray-200 pl-2 text-xs leading-4 text-gray-500 dark:border-gray-700 dark:text-gray-400"
				>
					<span class="line-clamp-3 whitespace-pre-wrap break-words">{fileAsk.text}</span>
				</blockquote>
			{/if}
			<div class="flex items-center gap-1.5">
				<textarea
					id="file-ask-input"
					rows="1"
					class="min-w-0 flex-1 resize-none border-0 bg-transparent p-0 text-sm leading-5 text-gray-700 outline-none placeholder:text-gray-400 focus:ring-0 dark:text-gray-300 dark:placeholder:text-gray-500"
					placeholder={$i18n.t('Ask or edit…')}
					aria-label={$i18n.t('Ask or edit…')}
					bind:value={fileAskInput}
					on:input={(event) => {
						event.currentTarget.style.height = 'auto';
						event.currentTarget.style.height = `${Math.min(96, event.currentTarget.scrollHeight)}px`;
					}}
					on:keydown={(event) => {
						if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
							event.preventDefault();
							submitFileAsk();
						}
					}}
				></textarea>
				<button
					type="submit"
					disabled={asking || !fileAskInput.trim()}
					aria-label={$i18n.t('Send')}
					class="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full transition-colors {fileAskInput.trim()
						? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100'
						: 'bg-gray-200 text-white dark:bg-gray-700 dark:text-gray-900'}"
				>
					{#if asking}<Spinner className="size-3.5" />{:else}<ArrowRight
							className="size-3.5 -rotate-90"
						/>{/if}
				</button>
			</div>
		</form>
	{/key}
{/if}
<ConfirmDialog
	show={dirtyChoice !== null}
	title={$i18n.t('Unsaved changes')}
	confirmLabel={dirtyChoice?.sending ? $i18n.t('Save and send') : $i18n.t('Save')}
	on:confirm={() => dirtyChoice?.resolve('save')}
	on:cancel={() => dirtyChoice?.resolve('cancel')}
>
	<p class="text-sm text-gray-500">{$i18n.t('Save your changes before continuing?')}</p>
	{#if dirtyChoice && !dirtyChoice.sending}<button
			type="button"
			class="mt-2 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
			on:click={() => dirtyChoice?.resolve('discard')}>{$i18n.t('Discard changes')}</button
		>{/if}
</ConfirmDialog>
