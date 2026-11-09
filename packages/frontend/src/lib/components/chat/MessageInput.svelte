<script lang="ts">
	import type { TerminalContext } from '$lib/types';
	import { estimateContextUsage } from '$lib/utils/contextUsage';
	import { resolveLocalizedFunction } from '$lib/utils/localizedContent';
	import { functions as localizedFunctions } from '$lib/stores';
	import DOMPurify from 'dompurify';
	import { toast } from 'svelte-sonner';

	import { marked } from 'marked';
	import { DOMParser } from 'prosemirror-model';
	import { Selection, TextSelection } from 'prosemirror-state';
	import { v4 as uuidv4 } from 'uuid';
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { onMount, tick, getContext, createEventDispatcher } from 'svelte';

	import { createPicker, getAuthToken } from '$lib/utils/google-drive-picker';
	import { pickAndDownloadFile } from '$lib/utils/onedrive-file-picker';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';

	const dispatch = createEventDispatcher();

	import {
		type Model,
		mobile,
		settings,
		models,
		config,
		showCallOverlay,
		tools,
		skills,
		terminalSkills,
		toolServers,
		terminalServers,
		user as _user,
		showTabs,
		showSettings,
		showFileNavDir,
		selectedTerminalId,
		TTSWorker,
		temporaryChatEnabled,
		sideChatRequest,
		visibleControls
	} from '$lib/stores';

	import {
		convertHeicToJpeg,
		isHeicImage,
		normalizeImageFile,
		compressImage,
		createMessagesList,
		extractContentFromFile,
		extractCurlyBraceWords,
		extractInputVariables,
		getAge,
		getCurrentDateTime,
		getFormattedDate,
		getFormattedTime,
		getUsageTokenCount,
		getUserPosition,
		getUserTimezone,
		getWeekday,
		isRasterImageContentType
	} from '$lib/utils';
	import { uploadFile } from '$lib/apis/files';
	import { getCwd, uploadNewFileToTerminal } from '$lib/apis/terminal';
	import { isSavedChatId } from '$lib/utils/chatId';
	import { generateAutoCompletion } from '$lib/apis';
	import { deleteFileById } from '$lib/apis/files';
	import { getChatById } from '$lib/apis/chats';
	import { getFolderById } from '$lib/apis/folders';
	import { getNoteById } from '$lib/apis/notes';
	import { getSessionUser } from '$lib/apis/auths';
	import { createFileInputLifecycle } from './fileInputLifecycle';
	import { createPromptVariableController } from './MessageInput/promptVariableController';
	import { createPromptInsertionController } from './MessageInput/promptInsertionController';
	import { createSlashPromptInsertionController } from './MessageInput/slashPromptInsertionController';
	import { createResourceDropController } from './MessageInput/resourceDropController';
	import {
		findFilesystemChatUploadTerminal,
		uploadFilesystemChatAttachment
	} from './MessageInput/filesystemUpload';
	import { createChatFileUploadController } from './MessageInput/chatFileUploadController';
	import { createChatInputKeyboardController } from './MessageInput/chatInputKeyboardController';
	import { isChatInputSendShortcut } from './MessageInput/chatInputSendShortcut';
	import { processComposerFile } from './MessageInput/fileProcessing';
	import { createAttachmentCollection } from './MessageInput/attachmentCollection';
	import { createSuggestionAttachmentHandler } from './MessageInput/suggestionAttachmentHandler';

	import { WEBUI_BASE_URL, WEBUI_API_BASE_URL, PASTED_TEXT_CHARACTER_LIMIT } from '$lib/constants';
	import { matchKeybinding, Shortcut } from '$lib/shortcuts';
	import { resolveLocalizedModelName } from '$lib/utils/localizedContent';

	import { createNoteHandler } from '../notes/utils';
	import { getSuggestionRenderer } from '../common/RichTextInput/suggestions';

	import InputMenu from './MessageInput/InputMenu.svelte';
	import VoiceRecording from './MessageInput/VoiceRecording.svelte';
	import ModelSelector from './ModelSelector.svelte';
	import ModelControls from './MessageInput/ModelControls.svelte';

	import ToolServersModal from './ToolServersModal.svelte';
	import SkillsModal from './SkillsModal.svelte';

	import RichTextInput from '../common/RichTextInput.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import FileItem from '../common/FileItem.svelte';
	import Image from '../common/Image.svelte';
	import Spinner from '../common/Spinner.svelte';

	import XMark from '../icons/XMark.svelte';
	import GlobeAlt from '../icons/GlobeAlt.svelte';
	import Photo from '../icons/Photo.svelte';
	import Wrench from '../icons/Wrench.svelte';
	import ArrowUpLeft from '../icons/ArrowUpLeft.svelte';
	import Cube from '../icons/Cube.svelte';
	import Sparkles from '../icons/Sparkles.svelte';
	import Mic from '../icons/Mic.svelte';

	import InputVariablesModal from './MessageInput/InputVariablesModal.svelte';
	import Voice from '../icons/Voice.svelte';
	import Terminal from '../icons/Terminal.svelte';
	import IntegrationsMenu from './MessageInput/IntegrationsMenu.svelte';
	import TerminalMenu from './MessageInput/TerminalMenu.svelte';
	import Component from '../icons/Component.svelte';
	import PlusAlt from '../icons/PlusAlt.svelte';
	import Dropdown from '../common/Dropdown.svelte';

	import CommandSuggestionList from './MessageInput/CommandSuggestionList.svelte';
	import Knobs from '../icons/Knobs.svelte';
	import CodeBrackets from '../icons/CodeBrackets.svelte';
	import ValvesModal from '../workspace/common/ValvesModal.svelte';
	import Note from '../icons/Note.svelte';
	import AskUserCard from './AskUserCard.svelte';
	import { goto } from '$app/navigation';
	import InputModal from '../common/InputModal.svelte';
	import Skeleton from './Messages/Skeleton.svelte';
	import Expand from '../icons/Expand.svelte';
	import QueuedMessageItem from './MessageInput/QueuedMessageItem.svelte';
	import TaskList from './Messages/ResponseMessage/TaskList.svelte';

	const i18n: any = getContext('i18n');

	type AskUserPrompt = {
		id?: string;
		show: boolean;
		questions: any[];
		allowOther: boolean;
		timeoutMs: number | null;
		onConfirm: (value: any) => void;
		onCancel: () => void;
	};

	export let onUpload: Function = (e) => {};
	export let onChange: Function = () => {};
	export let typingUsers: { id: string; name: string }[] = [];
	export let files: any[] = [];
	const attachmentCollection = createAttachmentCollection<any>(
		() => files,
		(nextFiles) => {
			files = nextFiles;
		}
	);
	const handleSuggestionUpload = createSuggestionAttachmentHandler({
		readFiles: () => files,
		collection: attachmentCollection,
		onUpload: (event) => onUpload(event)
	});
	const handleSuggestionUploadWithFilesystem = createSuggestionAttachmentHandler({
		readFiles: () => files,
		collection: attachmentCollection,
		includeFilesystem: true,
		onUpload: (event) => onUpload(event)
	});

	const handleTypingInput = (event: Event) => {
		const input = event.target as HTMLElement;
		if (input.isContentEditable) dispatch('typing', Boolean(input.textContent?.trim()));
	};
	export let onWebSearchToggle: Function = () => {};

	export let createMessagePair: Function;
	export let stopResponse: Function;
	export let compactHandler: Function = () => {};
	export let statusHandler: Function = () => {};
	export let forkHandler: Function = () => {};
	export let chatId = '';
	export let inputId = 'chat-input';
	export let messagesContainerId = 'messages-container';
	export let temporary: boolean | undefined = undefined;
	$: isTemporary = temporary ?? $temporaryChatEnabled;
	export let terminalContext: TerminalContext | null = null;
	export let terminalSelectionEnabled = true;
	export let contextUsage = null;
	export let contextCompactionEnabled = false;
	export let embedded = false;
	export let callActive = false;
	export let callEnabled = true;

	export let autoScroll = false;
	export let generating = false;

	export let atSelectedModel: Model | undefined = undefined;
	export let selectedModels: string[];
	export let multipleModels = true;
	export let onModelSelect: (models: string[]) => void = () => {};
	export let chatFiles: any[] = [];
	export let params: Record<string, any> = {};
	export let onParamChange: (key: string, value: unknown) => void = () => {};

	let selectedModelIds = [];
	let inputMenuComponent;
	$: selectedModelIds = atSelectedModel !== undefined ? [atSelectedModel.id] : selectedModels;
	$: hasChatVariables = selectedModelIds.some(
		(modelId) =>
			($models.find((model) => model.id === modelId)?.info?.meta?.chat_variables_schema?.fields
				?.length ?? 0) > 0
	);

	export let history;
	export let taskIds = null;
	export let askUser: AskUserPrompt = {
		show: false,
		questions: [],
		allowOther: true,
		timeoutMs: null,
		onConfirm: (_value: any) => {},
		onCancel: () => {}
	};

	$: isActive =
		!askUser?.show &&
		((taskIds && taskIds.length > 0) ||
			(history.currentId && history.messages[history.currentId]?.done != true) ||
			generating);
	$: canCompact = !!history?.currentId;
	$: canSide =
		($_user?.role === 'admin' ||
			($_user?.role === 'user' &&
				($_user.permissions?.chat?.temporary ?? true) &&
				($_user.permissions?.chat?.side_chat ?? false))) &&
		!embedded &&
		selectedModels.length === 1 &&
		!!selectedModelIds[0] &&
		!!chatId &&
		!!history?.currentId;
	$: canComments = !!(
		!embedded &&
		$config?.features?.enable_comments &&
		$_user &&
		isSavedChatId(chatId) &&
		!isTemporary
	);
	export const openComments = () => {
		if (canComments) showTabs.set('comments');
	};
	$: hasChatContent = history?.currentId
		? createMessagesList(history, history.currentId).some(
				(message) =>
					!!message?.content || !!message?.tool_calls || !!message?.files || !!message?.models
			)
		: false;
	$: canToggleTemporary =
		!embedded &&
		!chatId &&
		($_user?.role === 'admin' ||
			($_user?.role === 'user' &&
				($_user?.permissions?.chat?.temporary ?? true) &&
				!($_user?.permissions?.chat?.temporary_enforced ?? false)));

	export let prompt = '';
	let pendingInputFileCount = 0;
	$: isProcessingInputFiles = pendingInputFileCount > 0;
	const fileInputLifecycle = createFileInputLifecycle((count) => {
		pendingInputFileCount = count;
	});

	const submitPrompt = (message = prompt) => {
		if (isProcessingInputFiles) return false;
		dispatch('submit', message);
		return true;
	};

	export let selectedToolIds: string[] = [];
	export let selectedSkillIds: string[] = [];
	export let selectedFilterIds: string[] = [];

	export let imageGenerationEnabled = false;
	export let webSearchEnabled = false;
	export let codeInterpreterEnabled = false;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: Function = () => {};

	export let pendingOAuthTools: {
		id: string;
		name?: string;
		serverId: string;
		authType?: string | null;
	}[] = [];
	export let oauthRedirectHandler: Function = () => {};

	let showTerminalMenu = false;

	export let messageQueue: { id: string; prompt: string; files: any[]; error?: string }[] = [];
	export let onQueueSendNow: (id: string) => void = () => {};
	export let onQueueEdit: (id: string) => void = () => {};
	export let onQueueDelete: (id: string) => void = () => {};
	export let onUpdate: (data?: { file?: any }) => void = () => {};
	export let chatTasks = [];

	let inputContent = null;

	let showInputVariablesModal = false;
	let inputVariablesModalCallback = (variableValues) => {};
	let inputVariables = {};
	let inputVariableValues = {};

	let showValvesModal = false;
	let showStatusPanel = false;
	let copiedStatusChatId = false;
	let selectedValvesType = 'tool'; // 'tool' or 'function'
	let selectedValvesItemId = null;
	let integrationsMenuCloseOnOutsideClick = true;

	$: if (!showValvesModal) {
		integrationsMenuCloseOnOutsideClick = true;
	}

	let chatInputDraft: any;
	// Approval mode is persisted in chat parameters and user settings, not input drafts.
	$: chatInputDraft = {
		prompt,
		files: files
			.filter((file) => file.type !== 'image' && !(file.file instanceof File))
			.map((file) => {
				return {
					...file,
					user: undefined,
					access_grants: undefined
				};
			}),
		selectedToolIds,
		selectedSkillIds,
		selectedFilterIds,
		imageGenerationEnabled,
		webSearchEnabled,
		codeInterpreterEnabled
	};

	$: onChange(chatInputDraft);

	const replaceVariables = (variables: Record<string, any>) => {
		console.log('Replacing variables:', variables);

		const chatInput = document.getElementById(inputId);

		if (chatInput) {
			chatInputElement.replaceVariables(variables);
			focus();
		}
	};

	const promptVariableController = createPromptVariableController({
		extractInputVariables,
		onVariablesDetected: (variables) => {
			inputVariables = variables;
		},
		onInputModalOpened: () => {
			showInputVariablesModal = true;
		},
		onInputValuesResolved: (values) => {
			inputVariableValues = { ...inputVariableValues, ...values };
			replaceVariables(inputVariableValues);
			showInputVariablesModal = false;
		},
		readClipboardText: () => navigator.clipboard.readText(),
		readClipboardItems: async () => Array.from(await navigator.clipboard.read()),
		onClipboardTextError: () => toast.error($i18n.t('Failed to read clipboard contents')),
		onClipboardItemsError: (error) => console.error('Failed to read clipboard items:', error),
		createClipboardImageFile: (blob, name, type) => new File([blob], name, { type }),
		onClipboardImage: (file) => inputFilesHandler([file]),
		getUserPosition,
		onLocationError: () => toast.error($i18n.t('Location access not allowed')),
		getSessionUser: (token) => getSessionUser(token),
		getToken: () => localStorage.token,
		getLanguage: () => localStorage.getItem('locale'),
		getAge,
		getFormattedDate,
		getFormattedTime,
		getCurrentDateTime,
		getUserTimezone,
		getWeekday
	});
	const inputVariableHandler = promptVariableController.resolveInputVariables;
	const textVariableHandler = promptVariableController.replaceTextVariables;
	inputVariablesModalCallback = promptVariableController.completeInputVariables;

	export const focus = (options: FocusOptions = {}) => {
		chatInputElement?.focus(options);
	};

	export const setText = (text: string = '') => {
		chatInputElement?.setText(text);
		if (!$showCallOverlay) {
			focus();
		}
	};

	export const setTextHandler = async (text?: string, cb?: (text: string) => void) => {
		const chatInput = document.getElementById(inputId);

		if (chatInput) {
			if (text !== '') {
				text = await textVariableHandler(text || '');
			}

			setText(text);

			if (text !== '') {
				text = await inputVariableHandler(text);
			}

			await tick();
			if (cb) await cb(text);
		}
	};

	export const showStatus = async () => {
		showStatusPanel = true;
		await tick();
		focus({ preventScroll: true });
	};

	const formatTokenCount = (value: number) => {
		if (value >= 1_000_000) return `${trimNumber(value / 1_000_000)}m`;
		if (value >= 1_000) return `${trimNumber(value / 1_000)}k`;
		return String(value ?? 0);
	};

	const trimNumber = (value: number) =>
		value >= 10 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, '');

	const getLocalContextUsage = (chatHistory: typeof history, system: string) => {
		if (!chatHistory?.currentId) {
			return null;
		}

		const messages = createMessagesList(chatHistory, chatHistory.currentId);
		if (!messages.length) {
			return null;
		}
		return estimateContextUsage(messages, system, null, getUsageTokenCount);
	};

	const copyStatusChatId = async () => {
		if (!chatId) return;
		await navigator.clipboard.writeText(chatId);
		copiedStatusChatId = true;
		setTimeout(() => {
			copiedStatusChatId = false;
		}, 1600);
	};

	$: statusContextUsage = contextUsage ?? getLocalContextUsage(history, $settings?.system ?? '');
	$: contextHasThreshold = Number(statusContextUsage?.threshold) > 0;
	$: contextPercent = contextHasThreshold
		? Math.max(0, Math.round(statusContextUsage?.percent ?? 0))
		: null;
	$: contextTokens = formatTokenCount(
		statusContextUsage?.estimated_tokens || statusContextUsage?.tokens || 0
	);
	$: contextValue = statusContextUsage
		? contextHasThreshold
			? `${contextPercent}% ${contextTokens}/${formatTokenCount(statusContextUsage.threshold)}`
			: `${contextTokens} ${$i18n.t('tokens')}`
		: $i18n.t('unknown');
	$: contextBarPercent = contextHasThreshold ? Math.min(contextPercent, 100) : 0;

	const getCommand = () => {
		const chatInput = document.getElementById(inputId);
		let word = '';

		if (chatInput) {
			word = chatInputElement?.getWordAtDocPos();
		}

		return word;
	};

	const replaceCommandWithText = (text) => {
		const chatInput = document.getElementById(inputId);
		if (!chatInput) return;

		chatInputElement?.replaceCommandWithText(text);
	};

	const temporaryHandler = async () => {
		if (!canToggleTemporary) return;

		if (($settings?.temporaryChatByDefault ?? false) && isTemporary) {
			await temporaryChatEnabled.set(null);
		} else {
			await temporaryChatEnabled.set(!isTemporary);
		}

		if (location.pathname !== '/' && !location.pathname.startsWith('/folders/')) {
			await goto('/');
		}

		if (isTemporary) {
			window.history.replaceState(window.history.state, '', '?temporary-chat=true');
		} else {
			window.history.replaceState(window.history.state, '', location.pathname);
		}
	};

	const createSkillHandler = async () => {
		prompt = '/skills:create';
		await tick();
		submitPrompt(prompt);
	};

	const promptInsertionController = createPromptInsertionController({
		getInputElement: () => document.getElementById(inputId),
		resolveTextVariables: textVariableHandler,
		getCommand: () => command,
		replaceCommandWithText,
		insertContent: (text) => {
			chatInputElement?.insertContent(text);
		},
		tick,
		resolveInputVariables: inputVariableHandler,
		getInputContainer: () => document.getElementById(`${inputId}-container`),
		focus,
		getPrompt: () => prompt,
		extractCurlyBraceWords
	});
	const insertTextAtCursor = promptInsertionController.insertTextAtCursor;

	const slashPromptInsertionController = createSlashPromptInsertionController({
		resolveTextVariables: textVariableHandler,
		getRichTextMode: () => $settings?.insertPromptAsRichText ?? false,
		renderMarkdown: (text) =>
			DOMPurify.sanitize(
				marked
					.parse(text, {
						breaks: true,
						gfm: true
					})
					.trim()
			),
		parseRichText: (html, schema) => {
			const tempDiv = document.createElement('div');
			tempDiv.innerHTML = html;
			return DOMParser.fromSchema(schema).parse(tempDiv);
		},
		createRichTextSelection: (state, position) =>
			Selection.near(state.doc.resolve(position)),
		createMultilineSelection: (state, position) =>
			TextSelection.near(state.doc.resolve(position)),
		createSingleLineSelection: (state, position) =>
			state.selection.constructor.near(state.doc.resolve(position)),
		tick,
		resolveInputVariables: inputVariableHandler,
		focus
	});
	const replaceSlashRangeWithPrompt =
		slashPromptInsertionController.replaceSlashRangeWithPrompt;

	let command = '';
	export let showCommands = false;
	$: showCommands =
		['/', '#', '@', '$', ':'].includes(command?.charAt(0)) || '\\#' === command?.slice(0, 2);
	let suggestions = null;
	$: atSelectedModelName = resolveLocalizedModelName(atSelectedModel, $i18n.language);

	let showTools = false;
	let showSkills = false;

	let loaded = false;
	let recording = false;

	let isComposing = false;
	// Safari has a bug where compositionend is not triggered correctly #16615
	// when using the virtual keyboard on iOS.
	let compositionEndedAt = -2e8;
	const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
	function inOrNearComposition(event: Event) {
		if (isComposing) {
			return true;
		}
		// On Safari, ignore one keydown within 500 ms of compositionend so confirming IME text adds no newline.
		// See https://www.stum.de/2016/06/24/handling-ime-events-in-javascript/.
		if (isSafari && Math.abs(event.timeStamp - compositionEndedAt) < 500) {
			compositionEndedAt = -2e8;
			return true;
		}
		return false;
	}

	let chatInputContainerElement;
	let chatInputElement: RichTextInput;
	let modelSelector;

	let filesInputElement;
	let commandsElement;

	let inputFiles: FileList | null = null;

	let showInputModal = false;
	let showComparison = false;
	let comparisonLoading = false;
	let ComparisonModal: typeof import('../common/DiffEditorModal.svelte').default | null = null;
	// Preserve the previous comparison description in the translation catalogs.
	// $i18n.t('Uses the left working copy as your prompt.');
	let comparisonTarget: {
		chatId: string;
		input: RichTextInput;
		content: string;
	} | null = null;

	const openComparison = async () => {
		if (comparisonLoading) return;
		comparisonLoading = true;
		const target = { chatId, input: chatInputElement };
		try {
			ComparisonModal = (await import('../common/DiffEditorModal.svelte')).default;
			if (!target.input || target.chatId !== chatId || target.input !== chatInputElement) return;
			comparisonTarget = { chatId, input: target.input, content: prompt };
			showComparison = true;
		} catch {
			toast.error($i18n.t('Could not open comparison. Please try again.'));
		} finally {
			comparisonLoading = false;
		}
	};

	const applyComparisonToPrompt = (text: string) => {
		if (
			!chatInputElement ||
			comparisonTarget?.chatId !== chatId ||
			comparisonTarget?.input !== chatInputElement ||
			comparisonTarget?.content !== prompt
		)
			throw new Error($i18n.t('The prompt has changed. Reopen the comparison before applying.'));
		// The editor's literal setter preserves mentions without expanding prompt variables.
		if (text !== comparisonTarget.content) chatInputElement.setText(text);
		void tick().then(() => focus());
	};

	export let dragged = false;
	export let dropzoneId = 'chat-pane';
	let shiftKey = false;

	let user = null;
	export let placeholder = '';

	type ModelCapability =
		| 'vision'
		| 'file_upload'
		| 'web_search'
		| 'image_generation'
		| 'code_interpreter'
		| 'terminal'
		| 'builtin_tools';
	type ModelCapabilitiesById = Record<string, Partial<Record<ModelCapability, boolean>>>;

	let modelCapabilitiesById: ModelCapabilitiesById = {};
	$: modelCapabilitiesById = Object.fromEntries(
		($models ?? []).map((model) => [model.id, model.info?.meta?.capabilities ?? {}])
	);

	const getCapableModelIds = (
		modelIds: string[],
		capability: ModelCapability,
		capabilitiesById: ModelCapabilitiesById
	) => modelIds.filter((id) => capabilitiesById[id]?.[capability] ?? true);

	let visionCapableModels = [];
	$: visionCapableModels = getCapableModelIds(selectedModelIds, 'vision', modelCapabilitiesById);

	let fileUploadCapableModels = [];
	$: fileUploadCapableModels = getCapableModelIds(
		selectedModelIds,
		'file_upload',
		modelCapabilitiesById
	);

	let fileUploadEnabled: boolean = true;
	$: fileUploadEnabled =
		(getFilesystemUploadTerminal($selectedTerminalId, $terminalServers, $settings) ||
			fileUploadCapableModels.length === selectedModelIds.length) &&
		($_user?.role === 'admin' || $_user?.permissions?.chat?.file_upload);

	let webSearchCapableModels = [];
	$: webSearchCapableModels = getCapableModelIds(
		selectedModelIds,
		'web_search',
		modelCapabilitiesById
	);

	let imageGenerationCapableModels = [];
	$: imageGenerationCapableModels = getCapableModelIds(
		selectedModelIds,
		'image_generation',
		modelCapabilitiesById
	);

	let codeInterpreterCapableModels = [];
	$: codeInterpreterCapableModels = getCapableModelIds(
		selectedModelIds,
		'code_interpreter',
		modelCapabilitiesById
	);

	let terminalCapableModels = [];
	$: terminalCapableModels = getCapableModelIds(
		selectedModelIds,
		'terminal',
		modelCapabilitiesById
	);
	$: hasDirectToolServerAccess =
		$config?.features?.enable_direct_integrations &&
		($_user?.role === 'admin' || ($_user?.permissions?.features?.direct_tool_servers ?? true));
	$: showTerminalSelector =
		terminalSelectionEnabled &&
		$config?.features?.enable_tool_servers &&
		terminalCapableModels.length > 0 &&
		(($terminalServers ?? []).some((t) => t.id) ||
			(hasDirectToolServerAccess &&
				(($terminalServers ?? []).some((t) => !t.id) ||
					($settings?.terminalServers ?? []).some((s) => s.url))));

	let canCreateSkill: boolean = false;
	$: canCreateSkill = selectedModelIds.every(
		(id) =>
			(modelCapabilitiesById[id]?.builtin_tools ?? true) &&
			($models.find((model) => model.id === id)?.info?.meta?.builtinTools?.skills ?? true)
	);

	let toggleFilters = [];
	$: toggleFilters = (atSelectedModel?.id ? [atSelectedModel.id] : selectedModels)
		.map((id) => ($models.find((model) => model.id === id) || {})?.filters ?? [])
		.reduce((acc, filters) => acc.filter((f1) => filters.some((f2) => f2.id === f1.id)));

	let showToolsButton = false;
	$: showToolsButton = ($tools ?? []).length > 0 || ($toolServers ?? []).length > 0;

	let showSkillsButton = false;
	$: showSkillsButton =
		($skills ?? []).some((skill) => skill.is_active) ||
		($terminalSkills ?? []).some((skill) => skill.is_active);

	let showWebSearchButton = false;
	$: showWebSearchButton =
		selectedModelIds.length === webSearchCapableModels.length &&
		$config?.features?.enable_web_search &&
		($_user.role === 'admin' || $_user?.permissions?.features?.web_search);

	let showImageGenerationButton = false;
	$: showImageGenerationButton =
		selectedModelIds.length === imageGenerationCapableModels.length &&
		$config?.features?.enable_image_generation &&
		($_user.role === 'admin' || $_user?.permissions?.features?.image_generation);

	let showCodeInterpreterButton = false;
	$: showCodeInterpreterButton =
		!$selectedTerminalId &&
		selectedModelIds.length === codeInterpreterCapableModels.length &&
		$config?.features?.enable_code_interpreter &&
		($_user.role === 'admin' || $_user?.permissions?.features?.code_interpreter);

	let showCallButton = false;
	$: showCallButton =
		($_user?.role === 'admin' || ($_user?.permissions?.chat?.call ?? true)) &&
		(callActive ||
			$config?.audio?.realtime?.enabled ||
			($config?.audio?.stt?.engine !== 'web' &&
				($_user?.role === 'admin' || ($_user?.permissions?.chat?.stt ?? true))));

	$: if (webSearchCapableModels.length < selectedModelIds.length) webSearchEnabled = false;
	$: if (imageGenerationCapableModels.length < selectedModelIds.length)
		imageGenerationEnabled = false;
	$: if (codeInterpreterCapableModels.length < selectedModelIds.length)
		codeInterpreterEnabled = false;
	$: {
		const filterIds = selectedFilterIds.filter((id) =>
			toggleFilters.some((filter: { id: string }) => filter.id === id)
		);
		if (filterIds.length !== selectedFilterIds.length) selectedFilterIds = filterIds;
	}

	// Disable code interpreter when terminal is active (mutually exclusive)
	$: if ($selectedTerminalId && codeInterpreterEnabled) {
		codeInterpreterEnabled = false;
	}

	// Clear selected terminal when model doesn't support terminal
	$: if (
		terminalSelectionEnabled &&
		inputId === 'chat-input' &&
		$selectedTerminalId &&
		selectedModelIds.length > 0 &&
		terminalCapableModels.length === 0
	) {
		selectedTerminalId.set(null);
	}

	const scrollToBottom = () => {
		const element = document.getElementById(messagesContainerId);
		element?.scrollTo({
			top: element.scrollHeight,
			behavior: 'smooth'
		});
	};

	const screenCaptureHandler = async () => {
		try {
			// Request screen media
			const mediaStream = await navigator.mediaDevices.getDisplayMedia({
				video: { cursor: 'never' },
				audio: false
			});
			// Borrow a video element for the selected screen; this is a one-frame production.
			const video = document.createElement('video');
			video.srcObject = mediaStream;
			// Ensure the video loads without affecting user experience or tab switching
			await video.play();
			// Match the canvas to the video; no surprise cropping in this photo booth.
			const canvas = document.createElement('canvas');
			canvas.width = video.videoWidth;
			canvas.height = video.videoHeight;
			// One frame, please. We are taking a screenshot, not directing a film.
			const context = canvas.getContext('2d');
			context.drawImage(video, 0, 0, canvas.width, canvas.height);
			// Screenshot captured. Stop the tracks before this becomes a live broadcast.
			mediaStream.getTracks().forEach((track) => track.stop());

			// bring back focus to this current tab, so that the user can see the screen capture
			window.focus();

			// Convert the canvas to a Base64 image URL
			const imageUrl = canvas.toDataURL('image/png');
			const blob = await (await fetch(imageUrl)).blob();
			const file = new File([blob], `screen-capture-${Date.now()}.png`, { type: 'image/png' });
			inputFilesHandler([file]);
			// Release the video stream reference; the photo booth is closed.
			video.srcObject = null;
		} catch (error) {
			// Handle any errors (e.g., user cancels screen sharing)
			console.error('Error capturing screen:', error);
		}
	};

	const getFilesystemUploadTerminal = (
		selectedId = $selectedTerminalId,
		servers: any[] | null = $terminalServers,
		settingsValue: any = $settings
	) => findFilesystemChatUploadTerminal(selectedId, servers, settingsValue);

	const uploadTerminalFile = async (fileItem, sessionId: string | null) =>
		uploadFilesystemChatAttachment(fileItem, sessionId, {
			findTerminal: (terminalId) => getFilesystemUploadTerminal(terminalId),
			getContextDirectory: (terminalId) =>
				terminalContext?.terminalId === terminalId ? terminalContext?.currentDirectory : null,
			getCwd,
			uploadFile: uploadNewFileToTerminal,
			isSavedChat: isSavedChatId,
			showFileNavDirectory: (path) => showFileNavDir.set(path),
			touchAttachments: () => attachmentCollection.touch(),
			translate: (message) => $i18n.t(message),
			showError: (message) => toast.error(message)
		});

	export const uploadPendingFiles = async (sessionId: string) => {
		if (!sessionId) return false;
		for (const fileItem of files.filter(
			(item) => item.status === 'pending' && item.file instanceof File
		)) {
			if (chatId !== sessionId) return false;
			if (!(await uploadTerminalFile(fileItem, sessionId))) {
				fileItem.status = 'pending';
				attachmentCollection.touch();
				return false;
			}
		}
		return !files.some((item) => item.status === 'pending' && item.file instanceof File);
	};

	const chatFileUploadController = createChatFileUploadController({
		canUpload: () =>
			$_user?.role === 'admin' || ($_user?.permissions?.chat?.file_upload ?? true),
		canUploadWithSelectedModels: () =>
			fileUploadCapableModels.length === selectedModelIds.length,
		getFilesystemTerminal: () => getFilesystemUploadTerminal(),
		getSelectedTerminalId: () => $selectedTerminalId,
		getChatId: () => chatId || null,
		isTemporaryChat: () => isTemporary,
		getDefaultUploadContext: () => $settings?.defaultUploadContext === 'full',
		getToken: () => localStorage.token,
		getAudioLanguage: () => $settings?.audio?.stt?.language,
		createId: uuidv4,
		translate: (message, values) => $i18n.t(message, values),
		showError: (message) => toast.error(message),
		showWarning: (message) => toast.warning(message),
		addAttachment: (fileItem) => attachmentCollection.add(fileItem),
		touchAttachments: () => attachmentCollection.touch(),
		removeAttachment: (predicate) => attachmentCollection.remove(predicate),
		uploadFilesystem: uploadTerminalFile,
		uploadToServer: uploadFile,
		extractContent: extractContentFromFile,
		onUpdate: (data) => onUpdate(data)
	});

	const uploadFileHandler = async (file, process = true, itemData = {}) =>
		fileInputLifecycle.track(() => chatFileUploadController.upload(file, process, itemData));

	const inputFilesHandler = async (inputFiles: File[]) => {
		console.log('Input files handler called with:', inputFiles);

		if (
			($config?.file?.max_count ?? null) !== null &&
			files.length + inputFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return;
		}

		const results = await fileInputLifecycle.process(inputFiles, async (inputFile) => {
			console.log('Processing file:', {
				name: inputFile.name,
				type: inputFile.type,
				size: inputFile.size,
				extension: inputFile.name.split('.').at(-1)
			});

			await processComposerFile(inputFile, {
				maximumFileSizeBytes:
					$config?.file?.max_size == null ? null : $config.file.max_size * 1024 * 1024,
				normalizeImageFile,
				isImage: (file) => isRasterImageContentType(file.type),
				isHeicImage,
				convertHeicToJpeg,
				compressImage: async (url, width, height) =>
					(await compressImage(url, width, height)) as string,
				settings: $settings,
				fileConfig: $config?.file,
				visionEnabled: visionCapableModels.length > 0,
				temporary: isTemporary,
				readImageAsDataUrl: (file) =>
					new Promise<string>((resolve, reject) => {
						const reader = new FileReader();
						reader.onload = (event) => resolve(event.target.result as string);
						reader.onerror = () => reject(reader.error ?? new Error('Failed to read image file.'));
						reader.onabort = () => reject(new Error('Reading the image file was aborted.'));
						reader.readAsDataURL(file);
					}),
				fetchImageBlob: async (url) => (await fetch(url)).blob(),
				createImageFile: (blob, name, type) => new File([blob], name, { type }),
				appendTemporaryImage: (url) => {
					attachmentCollection.add({ type: 'image', url });
				},
				uploadFile: (file, process) => uploadFileHandler(file, process),
				onFileTooLarge: () => {
					console.log('File exceeds max size limit:', {
						fileSize: inputFile.size,
						maxSize: $config?.file?.max_size * 1024 * 1024
					});
					toast.error(
						$i18n.t(`File size should not exceed {{maxSize}} MB.`, {
							maxSize: $config?.file?.max_size
						})
					);
				},
				onVisionUnsupported: () =>
					toast.error($i18n.t('Selected model(s) do not support image inputs'))
			});
		});
		for (const result of results) {
			if (result.status === 'rejected') {
				console.error('File input processing failed:', result.reason);
				toast.error(`${result.reason}`);
			}
		}
	};

	const createNote = async () => {
		if (inputContent?.md.trim() === '' && inputContent?.html.trim() === '') {
			toast.error($i18n.t('Cannot create an empty note.'));
			return;
		}

		const res = await createNoteHandler(
			dayjs().locale($i18n.language).format('YYYY-MM-DD'),
			inputContent?.md,
			inputContent?.html
		);

		if (res) {
			// The draft has become a note; session storage can stop keeping a spare.
			sessionStorage.removeItem('chat-input');
			goto(`/notes/${res.id}`);
		}
	};

	const resourceDropController = createResourceDropController({
		getToken: () => localStorage.token,
		getChat: getChatById,
		getFolder: getFolderById,
		getNote: getNoteById,
		findModel: (id) => $models.find((model) => model.id === id),
		addAttachmentIfMissing: (item, isDuplicate) =>
			attachmentCollection.addIfMissing(item, isDuplicate),
		selectModel: (model) => {
			atSelectedModel = model;
		},
		handleFiles: (files) => inputFilesHandler(files),
		setDragging: (value) => {
			dragged = value;
		},
		logDrop: (event) => console.log(event),
		logFiles: (files) => console.log(files)
	});
	const onDragOver = resourceDropController.handleDragOver;
	const onDragLeave = resourceDropController.handleDragLeave;
	const onDrop = resourceDropController.handleDrop;

	const keyboardController = createChatInputKeyboardController({
		areShortcutsEnabled: () => $settings?.keyboardShortcuts !== false,
		isRecording: () => recording,
		getInputId: () => inputId,
		getActiveComposerId: () =>
			document.activeElement?.closest('[data-composer-id]')?.getAttribute('data-composer-id') ??
			'chat-input',
		matchKeybinding,
		dictationKeybinding: Shortcut.TOGGLE_DICTATION,
		setShiftKey: (value) => (shiftKey = value),
		clickElementById: (id) => document.getElementById(id)?.click(),
		setDragged: (value) => (dragged = value),
		logEscape: () => console.log('Escape')
	});
	const onKeyDown = keyboardController.onKeyDown;
	const onKeyUp = keyboardController.onKeyUp;

	const onFocus = () => {};
	const onBlur = keyboardController.onBlur;

	onMount(() => {
		suggestions = [
			{
				char: '@',
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					fileUploadEnabled: () => fileUploadEnabled,
					onSelect: (e) => {
						const { type, data } = e;

						if (type === 'model') {
							atSelectedModel = data;
						}

						focus({ preventScroll: true });
					},

					insertTextHandler: insertTextAtCursor,
					onUpload: handleSuggestionUploadWithFilesystem
				})
			},
			{
				char: '/',
				command: ({ editor, range, props }) => {
					if (props?.type === 'prompt') {
						void replaceSlashRangeWithPrompt(editor, range, props.content ?? '');
						return;
					}

					if (
						[
							'compact',
							'fork',
							'status',
							'comments',
							'side',
							'model',
							'compare',
							'settings',
							'files',
							'temporary',
							'skills:create'
						].includes(props?.id)
					) {
						editor.chain().focus().deleteRange(range).run();
						return;
					}

					editor
						.chain()
						.focus()
						.insertContentAt(range, [
							{
								type: 'mention',
								attrs: { ...props, mentionSuggestionChar: '/' }
							},
							{ type: 'text', text: ' ' }
						])
						.run();
				},
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					canCompact: () => !!history?.currentId && contextCompactionEnabled,
					compactDisabled: () => isActive,
					canStatus: () => !!history?.currentId,
					canComments: () => canComments,
					canSide: () => canSide,
					canFork: () =>
						!!history?.currentId &&
						!isTemporary &&
						($_user?.role === 'admin' || ($_user?.permissions?.chat?.import ?? true)),
					forkDisabled: () => isActive,
					canTemporary: () => canToggleTemporary,
					hasChatContent: hasChatContent,
					canCreateSkill: () => canCreateSkill,
					temporaryEnabled: () => isTemporary === true,
					contextUsage: () => statusContextUsage,
					onCompact: compactHandler,
					onStatus: statusHandler,
					onComments: openComments,
					onSide: () => {
						if (!canSide) return;
						sideChatRequest.set({ parentId: chatId, prompt: '' });
						showTabs.set('side');
					},
					onFork: forkHandler,
					onModel: () => modelSelector?.open(),
					onCompare: openComparison,
					onFiles: () => inputMenuComponent?.openPersonalFiles(),
					onSettings: () => showSettings.set(true),
					onTemporary: temporaryHandler,
					onCreateSkill: createSkillHandler,
					onSelect: (e) => {
						const { type, data } = e;

						if (type === 'model') {
							atSelectedModel = data;
						}

						focus({ preventScroll: true });
					},

					insertTextHandler: insertTextAtCursor,
					onUpload: handleSuggestionUpload
				})
			},
			{
				char: '#',
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					fileUploadEnabled: () => fileUploadEnabled,
					onSelect: (e) => {
						const { type, data } = e;

						if (type === 'model') {
							atSelectedModel = data;
						}

						focus({ preventScroll: true });
					},

					insertTextHandler: insertTextAtCursor,
					onUpload: handleSuggestionUpload
				})
			},
			{
				char: '$',
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					onSelect: (e) => {
						focus({ preventScroll: true });
					},

					insertTextHandler: insertTextAtCursor,
					onUpload: () => {}
				})
			},
			{
				char: ':',
				allowSpaces: false,
				command: ({ editor, range, props }) => {
					// Convert the Unicode hex codepoint (e.g. "1F44B") to the actual emoji character (👋)
					const codepoint = props.id;
					const emoji = codepoint
						.split('-')
						.map((cp) => String.fromCodePoint(parseInt(cp, 16)))
						.join('');
					editor.chain().focus().deleteRange(range).insertContent(emoji).run();
				},
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					onSelect: (e) => {
						focus({ preventScroll: true });
					},

					insertTextHandler: insertTextAtCursor,
					onUpload: () => {}
				})
			}
		];
		loaded = true;

		window.setTimeout(() => {
			if (!$showCallOverlay) {
				const chatInput = document.getElementById(inputId);
				chatInput?.focus();
			}
		}, 0);

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('focus', onFocus);
		window.addEventListener('blur', onBlur);

		let isDestroyed = false;
		let dropzoneElement: HTMLElement | null = null;
		const initialize = async () => {
			await tick();
			if (isDestroyed) return;

			dropzoneElement = document.getElementById(dropzoneId);
			if (dropzoneElement) {
				dropzoneElement.addEventListener('dragover', onDragOver, true);
				dropzoneElement.addEventListener('drop', onDrop, true);
				dropzoneElement.addEventListener('dragleave', onDragLeave);
			}
		};
		initialize();

		return () => {
			isDestroyed = true;

			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);

			window.removeEventListener('focus', onFocus);
			window.removeEventListener('blur', onBlur);

			if (dropzoneElement) {
				dropzoneElement.removeEventListener('dragover', onDragOver, true);
				dropzoneElement.removeEventListener('drop', onDrop, true);
				dropzoneElement.removeEventListener('dragleave', onDragLeave);
			}
		};
	});
</script>

{#if showComparison && ComparisonModal}
	<svelte:component
		this={ComparisonModal}
		bind:show={showComparison}
		title={$i18n.t('Compare text')}
		initialContent={comparisonTarget?.content ?? ''}
		applyLabel={$i18n.t('Use in prompt')}
		applyDescription={$i18n.t('Uses the proposed revision as your prompt.')}
		discardMessage={$i18n.t(
			'Your comparison changes will be discarded. Your prompt will stay unchanged.'
		)}
		onApply={applyComparisonToPrompt}
	/>
{/if}

<ToolServersModal
	bind:show={showTools}
	{selectedToolIds}
	onConnect={(id) =>
		oauthRedirectHandler({ id, serverId: id.split(':').at(-1), authType: 'mcp' }, chatInputDraft)}
/>
<SkillsModal bind:show={showSkills} {selectedSkillIds} />

<InputVariablesModal
	bind:show={showInputVariablesModal}
	variables={inputVariables}
	onSave={inputVariablesModalCallback}
/>

<ValvesModal
	bind:show={showValvesModal}
	userValves={true}
	type={selectedValvesType}
	id={selectedValvesItemId ?? null}
	on:save={async () => {
		await tick();
	}}
	on:close={() => {
		integrationsMenuCloseOnOutsideClick = true;
	}}
/>

<InputModal
	bind:show={showInputModal}
	oninput={handleTypingInput}
	bind:value={prompt}
	bind:inputContent
	onChange={(content) => {
		console.log(content);
		chatInputElement?.setContent(content?.json ?? null);
	}}
	onClose={async () => {
		await tick();
		focus();
	}}
/>

{#if loaded}
	<div class="w-full">
		<div class=" mx-auto inset-x-0 bg-transparent flex justify-center">
			<div
				class="flex flex-col px-3 {($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-[58rem]'} w-full"
			>
				<div class="relative">
					{#if typingUsers.length > 0}
						<div
							class="absolute -top-7 inset-x-0 pointer-events-none select-none bg-linear-to-t from-white dark:from-gray-900 to-transparent pb-2.5"
						>
							<div class="text-xs px-1 mt-1.5 flex items-center gap-1.5">
								<Skeleton size="xs" />
								<div class="min-w-0 truncate">
									<span class="font-normal text-black dark:text-white">
										{typingUsers.map((user) => user.name).join(', ')}
									</span>
									{typingUsers.length === 1 ? $i18n.t('is typing...') : $i18n.t('are typing...')}
								</div>
							</div>
						</div>
					{/if}
					{#if autoScroll === false && history?.currentId}
						<div
							class=" absolute -top-12 left-0 right-0 flex justify-center z-30 pointer-events-none"
						>
							<button
								aria-label={$i18n.t('Scroll to bottom')}
								class=" bg-white border border-gray-100 dark:border-none dark:bg-white/20 p-1.5 rounded-full pointer-events-auto"
								on:click={() => {
									autoScroll = true;
									scrollToBottom();
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="w-5 h-5"
								>
									<path
										fill-rule="evenodd"
										d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
										clip-rule="evenodd"
									/>
								</svg>
							</button>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<div class="bg-transparent">
			<div
				class="{($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-[58rem]'} px-2 mx-auto inset-x-0"
			>
				<div class="">
					<input
						bind:this={filesInputElement}
						bind:files={inputFiles}
						type="file"
						hidden
						multiple
						on:change={async () => {
							if (inputFiles && inputFiles.length > 0) {
								const _inputFiles = Array.from(inputFiles);
								inputFilesHandler(_inputFiles);
							} else {
								toast.error($i18n.t(`File not found.`));
							}

							filesInputElement.value = '';
						}}
					/>

					<div class={recording ? '' : 'hidden'}>
						<VoiceRecording
							confirmButtonId={inputId === 'chat-input'
								? 'confirm-recording-button'
								: `${inputId}-confirm-recording-button`}
							bind:recording
							onCancel={async () => {
								recording = false;

								await tick();
								focus({ preventScroll: true });
							}}
							onConfirm={async (data) => {
								const { text, filename } = data;

								recording = false;

								await tick();
								await insertTextAtCursor(`${text}`);
								await tick();
								focus({ preventScroll: true });

								if ($settings?.speechAutoSend ?? false) {
									submitPrompt(prompt);
								}
							}}
						/>
					</div>
					<form
						class="w-full flex flex-col gap-1.5 {recording ? 'hidden' : ''}"
						on:submit|preventDefault={() => {
							submitPrompt(prompt);
						}}
					>
						<button
							id={inputId === 'chat-input'
								? 'generate-message-pair-button'
								: `${inputId}-generate-message-pair-button`}
							type="button"
							aria-label={$i18n.t('Generate message pair')}
							class="hidden"
							on:click={() => !isProcessingInputFiles && createMessagePair(prompt)}
						></button>

						{#if askUser?.show}
							<div class="mx-1">
								{#key askUser.id}
									{@const activeAskUser = askUser}
									<AskUserCard
										show={askUser.show}
										questions={askUser.questions}
										allowOther={askUser.allowOther}
										timeoutMs={askUser.timeoutMs}
										on:confirm={(e) => {
											activeAskUser.onConfirm(e.detail);
										}}
										on:cancel={() => {
											activeAskUser.onCancel();
										}}
									/>
								{/key}
							</div>
						{/if}

						<!-- Task list display -->
						{#if isActive && chatTasks.length > 0}
							<div class="mx-1">
								<TaskList tasks={chatTasks} />
							</div>
						{/if}

						<!-- Queued messages display -->
						{#if messageQueue.length > 0}
							<div
								class="mb-1 mx-2 py-0.5 px-1.5 rounded-2xl bg-white dark:bg-gray-900/60 border border-gray-100 dark:border-gray-800/50 overflow-x-hidden overflow-y-auto max-h-[25vh]"
							>
								{#each messageQueue as queuedMessage (queuedMessage.id)}
									<QueuedMessageItem
										id={queuedMessage.id}
										content={queuedMessage.prompt}
										error={queuedMessage.error ?? ''}
										files={queuedMessage.files}
										onSendNow={onQueueSendNow}
										onEdit={onQueueEdit}
										onDelete={onQueueDelete}
									/>
								{/each}
							</div>
						{/if}

						{#if showStatusPanel}
							<div class="mx-1 rounded-2xl bg-white text-xs dark:bg-gray-900">
								<div class="flex items-center justify-between px-3 py-1.5">
									<div class="flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300">
										<span>{$i18n.t('Status')}</span>
									</div>

									<button
										type="button"
										class="text-xs text-gray-400 transition-colors hover:text-gray-600 dark:hover:text-gray-300"
										on:click={() => {
											showStatusPanel = false;
										}}
									>
										{$i18n.t('Close')}
									</button>
								</div>

								<div class="space-y-0.5 px-3 pb-2">
									<div class="rounded-xl py-0.5 text-gray-600 dark:text-gray-400">
										<div class="flex min-h-4 items-center gap-3">
											<span class="min-w-0 flex-1 truncate">{$i18n.t('Context usage')}</span>
											<span
												class="shrink-0 font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
											>
												{contextValue}
											</span>
										</div>
										{#if contextHasThreshold}
											<div
												class="mt-1.5 h-0.5 overflow-hidden rounded-full bg-gray-100 dark:bg-white/8"
											>
												<div
													class="h-full rounded-full bg-gray-300 dark:bg-white/20"
													style={`width: ${contextBarPercent}%`}
												></div>
											</div>
										{/if}
									</div>

									{#if messageQueue.length}
										<div class="flex min-h-5 items-center gap-3 text-gray-600 dark:text-gray-400">
											<span class="min-w-0 flex-1 truncate">{$i18n.t('Queued messages')}</span>
											<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
												{messageQueue.length}
											</span>
										</div>
									{/if}

									{#if chatTasks.length}
										<div class="flex min-h-5 items-center gap-3 text-gray-600 dark:text-gray-400">
											<span class="min-w-0 flex-1 truncate">{$i18n.t('Tasks')}</span>
											<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
												{chatTasks.length}
											</span>
										</div>
									{/if}

									<div class="flex min-h-5 items-center gap-3 text-gray-600 dark:text-gray-400">
										<span class="min-w-0 flex-1 truncate">{$i18n.t('Chat ID')}</span>
										{#if chatId}
											<button
												type="button"
												class="min-w-0 max-w-[18rem] truncate font-mono text-[0.625rem] text-gray-400 underline-offset-2 transition-colors duration-75 hover:text-gray-700 hover:underline dark:text-gray-600 dark:hover:text-gray-200"
												on:click={copyStatusChatId}
											>
												{copiedStatusChatId ? $i18n.t('Copied') : chatId}
											</button>
										{:else}
											<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
												none
											</span>
										{/if}
									</div>
								</div>
							</div>
						{/if}

						<div
							id={inputId === 'chat-input' ? 'message-input-container' : `${inputId}-wrapper`}
							class="flex-1 flex flex-col relative w-full shadow-lg rounded-3xl border {isTemporary
								? 'border-dashed border-gray-100 dark:border-gray-800 hover:border-gray-200 focus-within:border-gray-200 hover:dark:border-gray-700 focus-within:dark:border-gray-700'
								: ' border-gray-100/50 dark:border-gray-850/50 hover:border-gray-200 focus-within:border-gray-100 hover:dark:border-gray-800 focus-within:dark:border-gray-800'} {($settings?.highContrastMode ??
							false)
								? 'focus-within:outline focus-within:outline-2 focus-within:-outline-offset-2 focus-within:outline-blue-500 [&_.ProseMirror:focus-visible]:outline-none!'
								: ''}  transition px-0.5 bg-white/5 dark:bg-gray-500/5 backdrop-blur-sm dark:text-gray-100"
							dir={$settings?.chatDirection ?? 'auto'}
						>
							{#if atSelectedModel !== undefined}
								<div class="px-2.5 pt-2.5 text-left w-full flex flex-col z-10">
									<div class="flex items-center justify-between w-full">
										<div class="pl-[0.0625rem] flex items-center gap-2 text-sm dark:text-gray-500">
											<img
												alt={$i18n.t('model profile')}
												class="size-3.5 max-w-[1.75rem] object-cover rounded-full"
												src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${$models.find((model) => model.id === atSelectedModel.id).id}&lang=${$i18n.language}`}
											/>
											<div class="translate-y-[0.5px]">
												<span class="">{atSelectedModelName}</span>
											</div>
										</div>
										<div>
											<button
												class="flex items-center dark:text-gray-500"
												aria-label={$i18n.t('Remove Selected Model')}
												on:click={() => {
													atSelectedModel = undefined;
												}}
											>
												<XMark />
											</button>
										</div>
									</div>
								</div>
							{/if}

							{#if files.length > 0}
								<div
									class="mx-2 mt-2 pb-1 flex items-center flex-wrap gap-1.5"
									dir={$settings?.chatDirection ?? 'auto'}
								>
									{#each files as file, fileIdx}
										{#if file.type === 'image' || isRasterImageContentType(file?.content_type)}
											{@const fileUrl =
												file.url.startsWith('data') || file.url.startsWith('http')
													? file.url
													: `${WEBUI_API_BASE_URL}/files/${file.url}${file?.content_type ? '/content' : ''}`}
											<div class=" relative group">
												<div class="relative flex items-center">
													<Image
														src={fileUrl}
														alt=""
														imageClassName=" size-10 rounded-xl object-cover"
													/>
													{#if selectedModelIds.length !== visionCapableModels.length}
														<Tooltip
															className=" absolute top-1 left-1"
															content={$i18n.t('{{ models }}', {
																models: selectedModelIds
																	.filter((id) => !visionCapableModels.includes(id))
																	.join(', ')
															})}
														>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 24 24"
																fill="currentColor"
																aria-hidden="true"
																class="size-4 fill-yellow-300"
															>
																<path
																	fill-rule="evenodd"
																	d="M9.401 3.003c1.155-2 4.043-2 5.197 0l7.355 12.748c1.154 2-.29 4.5-2.599 4.5H4.645c-2.309 0-3.752-2.5-2.598-4.5L9.4 3.003ZM12 8.25a.75.75 0 0 1 .75.75v3.75a.75.75 0 0 1-1.5 0V9a.75.75 0 0 1 .75-.75Zm0 8.25a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z"
																	clip-rule="evenodd"
																/>
															</svg>
														</Tooltip>
													{/if}
												</div>
												<div class=" absolute -top-1 -right-1">
													<button
														class=" bg-white text-black border border-white rounded-full {($settings?.highContrastMode ??
														false)
															? ''
															: 'hover-reveal transition'}"
														type="button"
														aria-label={$i18n.t('Remove file')}
														on:click={() => {
															attachmentCollection.removeAt(fileIdx);
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 20 20"
															fill="currentColor"
															aria-hidden="true"
															class="size-4"
														>
															<path
																d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
															/>
														</svg>
													</button>
												</div>
											</div>
										{:else}
											<FileItem
												item={file}
												name={file.name}
												type={file.type}
												size={file?.size}
												loading={file.status === 'uploading'}
												dismissible={true}
												edit={true}
												small={true}
												modal={['file', 'collection'].includes(file?.type)}
												on:dismiss={async () => {
													// Remove from UI state
													attachmentCollection.removeAt(fileIdx);
												}}
												on:click={() => {
													console.log(file);
												}}
											/>
										{/if}
									{/each}
								</div>
							{/if}

							<div class="px-2 relative">
								{#if prompt.split('\n').length > 2}
									<button
										type="button"
										class="absolute top-2.5 right-3 z-20 p-1 rounded-lg hover:bg-gray-100/50 dark:hover:bg-gray-800/50"
										aria-label={$i18n.t('Expand input')}
										on:click={() => {
											showInputModal = true;
										}}
									>
										<Expand />
									</button>
								{/if}

								<div
									class="scrollbar-hidden rtl:text-right ltr:text-left bg-transparent dark:text-gray-100 outline-hidden w-full pb-0.5 px-1 resize-none h-fit max-h-96 overflow-auto {files.length ===
									0
										? atSelectedModel !== undefined
											? 'pt-1'
											: 'pt-2'
										: ''}"
									id={`${inputId}-container`}
									data-composer-id={inputId}
									on:input={handleTypingInput}
								>
									{#if suggestions}
										{#key $settings?.richTextInput ?? true}
											{#key $settings?.showFormattingToolbar ?? false}
												<RichTextInput
													bind:this={chatInputElement}
													id={inputId}
													editable={!showInputModal}
													onChange={(content) => {
														prompt = content.md;
														inputContent = content;
														command = getCommand();
													}}
													json={true}
													richText={$settings?.richTextInput ?? true}
													messageInput={true}
													followUpSuggestion={!generating &&
													history?.messages?.[history?.currentId]?.role === 'assistant' &&
													history?.messages?.[history?.currentId]?.done
														? (history.messages[history.currentId].followUps?.[0] ?? '')
														: ''}
													showFormattingToolbar={$settings?.showFormattingToolbar ?? false}
													floatingMenuPlacement={'top-start'}
													insertPromptAsRichText={$settings?.insertPromptAsRichText ?? false}
													shiftEnter={!($settings?.ctrlEnterToSend ?? false) &&
														!$mobile &&
														!(
															'ontouchstart' in window ||
															navigator.maxTouchPoints > 0 ||
															navigator.msMaxTouchPoints > 0
														)}
													placeholder={placeholder ? placeholder : $i18n.t('Send a Message')}
													largeTextAsFile={($settings?.largeTextAsFile ?? false) && !shiftKey}
													autocomplete={$config?.features?.enable_autocomplete_generation &&
														($settings?.promptAutocomplete ?? false)}
													generateAutoCompletion={async (text) => {
														if (selectedModelIds.length === 0 || !selectedModelIds.at(0)) {
															return null;
														}

														const res = await generateAutoCompletion(
															localStorage.token,
															selectedModelIds.at(0),
															text,
															history?.currentId
																? createMessagesList(history, history.currentId)
																: null,
															'general'
														).catch((error) => {
															console.log(error);

															return null;
														});

														console.log(res);
														return res;
													}}
													{suggestions}
													oncompositionstart={() => (isComposing = true)}
													oncompositionend={(e) => {
														compositionEndedAt = e.timeStamp;
														isComposing = false;
													}}
													on:keydown={async (e) => {
														const keyEvent = e.detail.event as unknown as KeyboardEvent;

														const suggestionsContainerElement =
															document.getElementById('suggestions-container');

														if (keyEvent.key === 'Escape') {
															stopResponse();
														}

														if (prompt === '' && keyEvent.key == 'ArrowUp') {
															keyEvent.preventDefault();

															const userMessageElement = [
																...(document
																	.getElementById(messagesContainerId)
																	?.getElementsByClassName('user-message') ?? [])
															]?.at(-1);

															if (userMessageElement) {
																userMessageElement.scrollIntoView({ block: 'center' });
																const editButton = [
																	...userMessageElement.getElementsByClassName(
																		'edit-user-message-button'
																	)
																]?.at(-1);

																editButton?.click();
															}
														}

														if (!suggestionsContainerElement) {
															if (
																!$mobile ||
																!(
																	'ontouchstart' in window ||
																	navigator.maxTouchPoints > 0 ||
																	navigator.msMaxTouchPoints > 0
																)
															) {
																if (inOrNearComposition(keyEvent)) {
																	return;
																}

																if (isChatInputSendShortcut(keyEvent, $settings?.ctrlEnterToSend ?? false)) {
																	keyEvent.preventDefault();
																	if (
																		!isProcessingInputFiles &&
																		(prompt !== '' || files.length > 0)
																	) {
																		submitPrompt(prompt);
																	}
																}
															}
														}

														if (
															$settings?.keyboardShortcuts !== false &&
															matchKeybinding(keyEvent) === Shortcut.CLEAR_INPUT_SELECTIONS
														) {
															console.log('Escape');
															atSelectedModel = undefined;
															selectedToolIds = [];
															selectedFilterIds = [];

															webSearchEnabled = false;
															imageGenerationEnabled = false;
															codeInterpreterEnabled = false;
														}
													}}
													on:paste={async (e) => {
														e = e.detail.event;
														console.log(e);

														const clipboardData = e.clipboardData || window.clipboardData;

														if (clipboardData && clipboardData.items) {
															for (const item of clipboardData.items) {
																if (item.type === 'text/plain') {
																	if (($settings?.largeTextAsFile ?? false) && !shiftKey) {
																		const text = clipboardData.getData('text/plain');

																		if (text.length > PASTED_TEXT_CHARACTER_LIMIT) {
																			e.preventDefault();
																			const blob = new Blob([text], { type: 'text/plain' });
																			const file = new File(
																				[blob],
																				`Pasted_Text_${Date.now()}.txt`,
																				{
																					type: 'text/plain'
																				}
																			);

																			await uploadFileHandler(file, true, { context: 'full' });
																		}
																	}
																} else {
																	const file = item.getAsFile();
																	if (file) {
																		await inputFilesHandler([file]);
																		e.preventDefault();
																	}
																}
															}
														}
													}}
												/>
											{/key}
										{/key}
									{/if}
								</div>
							</div>

							<div class=" flex justify-between mt-0.5 mb-2 mx-0.5 max-w-full" dir="ltr">
								<div class="ml-1 self-end flex items-center">
									<InputMenu
										bind:this={inputMenuComponent}
										onCompare={openComparison}
										{chatId}
										bind:files
										selectedModels={selectedModelIds}
										fileUploadCapableModels={getFilesystemUploadTerminal(
											$selectedTerminalId,
											$terminalServers,
											$settings
										)
											? selectedModelIds
											: fileUploadCapableModels}
										{visionCapableModels}
										{toolApprovalMode}
										{onToolApprovalModeChange}
										{screenCaptureHandler}
										{inputFilesHandler}
										uploadFilesHandler={() => {
											filesInputElement.click();
										}}
										uploadGoogleDriveHandler={async () => {
											try {
												const fileData = await createPicker();
												if (fileData) {
													const file = new File([fileData.blob], fileData.name, {
														type: fileData.blob.type
													});
													await uploadFileHandler(file);
												} else {
													console.log('No file was selected from Google Drive');
												}
											} catch (error) {
												console.error('Google Drive Error:', error);
												toast.error(
													$i18n.t('Error accessing Google Drive: {{error}}', {
														error: error.message
													})
												);
											}
										}}
										uploadOneDriveHandler={async (authorityType) => {
											try {
												const fileData = await pickAndDownloadFile(authorityType);
												if (fileData) {
													const file = new File([fileData.blob], fileData.name, {
														type: fileData.blob.type || 'application/octet-stream'
													});
													await uploadFileHandler(file);
												} else {
													console.log('No file was selected from OneDrive');
												}
											} catch (error) {
												console.error('OneDrive Error:', error);
											}
										}}
										{onUpload}
										onClose={async () => {
											await tick();

											const chatInput = document.getElementById(inputId);
											chatInput?.focus();
										}}
									>
										<button
											type="button"
											id="input-menu-button"
											class="bg-transparent hover:bg-gray-100/40 text-gray-700 dark:text-white dark:hover:bg-gray-800/40 rounded-full size-[1.875rem] flex justify-center items-center outline-hidden focus:outline-hidden shrink-0"
											aria-label={$i18n.t('More')}
										>
											<PlusAlt className="size-5" />
										</button>
									</InputMenu>

									{#if showWebSearchButton || showImageGenerationButton || showCodeInterpreterButton || showToolsButton || showSkillsButton || (toggleFilters && toggleFilters.length > 0)}
										<div
											class="flex self-center w-[0.0625rem] h-4 mx-1 bg-gray-200/50 dark:bg-gray-800/50 shrink-0"
										></div>

										<IntegrationsMenu
											selectedModels={selectedModelIds}
											{toggleFilters}
											{showWebSearchButton}
											{showImageGenerationButton}
											{showCodeInterpreterButton}
											bind:selectedToolIds
											bind:selectedSkillIds
											bind:selectedFilterIds
											bind:webSearchEnabled
											bind:imageGenerationEnabled
											bind:codeInterpreterEnabled
											oauthRedirectHandler={(tool: {
												id: string;
												serverId: string;
												authType?: string | null;
											}) => oauthRedirectHandler(tool, chatInputDraft)}
											{onWebSearchToggle}
											closeOnOutsideClick={integrationsMenuCloseOnOutsideClick}
											onShowValves={(e) => {
												const { type, id } = e;
												selectedValvesType = type;
												selectedValvesItemId = id;
												showValvesModal = true;
												integrationsMenuCloseOnOutsideClick = false;
											}}
											onClose={async () => {
												await tick();

												if (!$mobile) {
													const chatInput = document.getElementById(inputId);
													chatInput?.focus();
												}
											}}
										>
											<button
												type="button"
												id="integration-menu-button"
												class="bg-transparent hover:bg-gray-100/40 text-gray-700 dark:text-white dark:hover:bg-gray-800/40 rounded-full size-[1.875rem] flex justify-center items-center outline-hidden focus:outline-hidden shrink-0"
												aria-label={$i18n.t('Integrations')}
											>
												<Component className="size-4.5" strokeWidth="1.5" />
											</button>
										</IntegrationsMenu>
									{/if}

									{#if hasChatVariables}
										<Tooltip content={$i18n.t('Chat Variables')} placement="top">
											<button
												type="button"
												id="chat-variables-button"
												class="bg-transparent hover:bg-gray-100 text-gray-700 dark:text-white dark:hover:bg-gray-800 rounded-full size-[1.875rem] flex justify-center items-center outline-hidden focus:outline-hidden shrink-0"
												aria-label={$i18n.t('Chat Variables')}
												on:click={() => {
													dispatch('chatVariables');
												}}
											>
												<CodeBrackets className="size-4.5" strokeWidth="1.5" />
											</button>
										</Tooltip>
									{/if}
								</div>

								<div class="flex flex-1 items-center min-w-0 overflow-x-auto scrollbar-none">
									{#if selectedModelIds.length === 1 && $models.find((m) => m.id === selectedModelIds[0])?.has_user_valves && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.valves ?? true))}
										<div class="ml-1 flex gap-1.5 shrink-0">
											<Tooltip content={$i18n.t('Valves')} placement="top">
												<button
													type="button"
													id="model-valves-button"
													aria-label={$i18n.t('Valves')}
													class="bg-transparent hover:bg-gray-100 text-gray-700 dark:text-white dark:hover:bg-gray-800 rounded-full size-[1.875rem] flex justify-center items-center outline-hidden focus:outline-hidden"
													on:click={() => {
														selectedValvesType = 'function';
														selectedValvesItemId = selectedModelIds[0]?.split('.')[0];
														showValvesModal = true;
													}}
												>
													<Knobs className="size-4" strokeWidth="1.5" />
												</button>
											</Tooltip>
										</div>
									{/if}

									<div class="ml-1 flex gap-1.5 shrink-0">
										{#if (selectedToolIds ?? []).length > 0 && $visibleControls['chat.selected_tools']}
											<Tooltip
												content={$i18n.t('{{COUNT}} Available Tools', {
													COUNT: (selectedToolIds ?? []).length
												})}
											>
												<button
													class="translate-y-[0.5px] px-1 flex gap-1 items-center text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg self-center transition"
													aria-label={$i18n.t('Available Tools')}
													type="button"
													on:click={() => {
														showTools = !showTools;
													}}
												>
													<Wrench className="size-4" strokeWidth="1.75" />

													<span class="text-sm">
														{(selectedToolIds ?? []).length}
													</span>
												</button>
											</Tooltip>
										{/if}

										{#if (selectedSkillIds ?? []).length > 0 && $visibleControls['chat.selected_skills']}
											<Tooltip
												content={$i18n.t('{{COUNT}} Available Skills', {
													COUNT: (selectedSkillIds ?? []).length
												})}
											>
												<button
													class="translate-y-[0.5px] px-1 flex gap-1 items-center text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg self-center transition"
													aria-label={$i18n.t('Available Skills')}
													type="button"
													on:click={() => {
														showSkills = !showSkills;
													}}
												>
													<Cube className="size-4" strokeWidth="1.75" />

													<span class="text-sm">
														{(selectedSkillIds ?? []).length}
													</span>
												</button>
											</Tooltip>
										{/if}

										{#each selectedFilterIds as filterId (filterId)}
											{@const filter = toggleFilters.find((f) => f.id === filterId)}
											{#if filter && $visibleControls['chat.selected_filters']}
												<Tooltip
													content={resolveLocalizedFunction(
														filter,
														$localizedFunctions,
														$i18n.language
													)}
													placement="top"
												>
													<button
														on:click|preventDefault={() => {
															if (
																filter?.has_user_valves &&
																($_user?.role === 'admin' ||
																	($_user?.permissions?.chat?.valves ?? true))
															) {
																selectedValvesType = 'function';
																selectedValvesItemId = filterId;
																showValvesModal = true;
															} else {
																selectedFilterIds = selectedFilterIds.filter(
																	(id) => id !== filterId
																);
															}
														}}
														type="button"
														class="group p-[0.375rem] flex gap-1.5 items-center text-sm rounded-full transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden {selectedFilterIds.includes(
															filterId
														)
															? 'text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-600/10 border border-sky-200/40 dark:border-sky-500/20'
															: 'bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 '} capitalize"
													>
														{#if filter?.icon}
															<div class="size-4 items-center flex justify-center">
																<img
																	src={filter.icon}
																	class="size-3.5 {filter.icon.includes('data:image/svg')
																		? 'dark:invert-[80%]'
																		: ''}"
																	style="fill: currentColor;"
																	alt={resolveLocalizedFunction(
																		filter,
																		$localizedFunctions,
																		$i18n.language
																	)}
																/>
															</div>
														{:else}
															<Sparkles className="size-4" strokeWidth="1.75" />
														{/if}
														<!-- svelte-ignore a11y-click-events-have-key-events -->
														<!-- svelte-ignore a11y-no-static-element-interactions -->
														<div
															class="hidden group-hover:block"
															on:click={(e) => {
																e.stopPropagation();
																e.preventDefault();
																selectedFilterIds = selectedFilterIds.filter(
																	(id) => id !== filterId
																);
															}}
														>
															<XMark className="size-4" strokeWidth="1.75" />
														</div>
													</button>
												</Tooltip>
											{/if}
										{/each}

										{#if webSearchEnabled && showWebSearchButton && $visibleControls['chat.selected_web_search']}
											<Tooltip content={$i18n.t('Web Search')} placement="top">
												<button
													aria-label={$i18n.t('Web Search')}
													aria-pressed={webSearchEnabled}
													on:click|preventDefault={() => (webSearchEnabled = !webSearchEnabled)}
													type="button"
													class="group p-[0.375rem] flex gap-1.5 items-center text-sm rounded-full transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden {webSearchEnabled ||
													($settings?.webSearch ?? false) === 'always'
														? ' text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-600/10 border border-sky-200/40 dark:border-sky-500/20'
														: 'bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 '}"
												>
													<GlobeAlt className="size-4" strokeWidth="1.75" />
													<div class="hidden group-hover:block">
														<XMark className="size-4" strokeWidth="1.75" />
													</div>
												</button>
											</Tooltip>
										{/if}

										{#if imageGenerationEnabled && showImageGenerationButton && $visibleControls['chat.selected_image_generation']}
											<Tooltip content={$i18n.t('Image')} placement="top">
												<button
													aria-label={$i18n.t('Image')}
													aria-pressed={imageGenerationEnabled}
													on:click|preventDefault={() =>
														(imageGenerationEnabled = !imageGenerationEnabled)}
													type="button"
													class="group p-[0.375rem] flex gap-1.5 items-center text-sm rounded-full transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden {imageGenerationEnabled
														? ' text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-700/10 border border-sky-200/40 dark:border-sky-500/20'
														: 'bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 '}"
												>
													<Photo className="size-4" strokeWidth="1.75" />
													<div class="hidden group-hover:block">
														<XMark className="size-4" strokeWidth="1.75" />
													</div>
												</button>
											</Tooltip>
										{/if}

										{#if codeInterpreterEnabled && showCodeInterpreterButton && $visibleControls['chat.selected_code_interpreter']}
											<Tooltip content={$i18n.t('Code Interpreter')} placement="top">
												<button
													aria-label={codeInterpreterEnabled
														? $i18n.t('Disable Code Interpreter')
														: $i18n.t('Enable Code Interpreter')}
													aria-pressed={codeInterpreterEnabled}
													on:click|preventDefault={() =>
														(codeInterpreterEnabled = !codeInterpreterEnabled)}
													type="button"
													class=" group p-[0.375rem] flex gap-1.5 items-center text-sm transition-colors duration-300 max-w-full overflow-hidden {codeInterpreterEnabled
														? ' text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-700/10 border border-sky-200/40 dark:border-sky-500/20'
														: 'bg-transparent text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 '} {($settings?.highContrastMode ??
													false)
														? 'm-1'
														: 'focus:outline-hidden rounded-full'}"
												>
													<Terminal className="size-3.5" strokeWidth="2" />

													<div class="hidden group-hover:block">
														<XMark className="size-4" strokeWidth="1.75" />
													</div>
												</button>
											</Tooltip>
										{/if}

										<!-- Terminal Server Selector -->
										{#if showTerminalSelector}
											<TerminalMenu
												bind:show={showTerminalMenu}
												disabled={generating ||
													(!!history?.currentId &&
														history.messages[history.currentId]?.done != true)}
											/>
										{/if}

										{#each pendingOAuthTools as pendingTool (pendingTool.id)}
											<Tooltip content={$i18n.t('Click to connect')} placement="top">
												<button
													aria-label={`${$i18n.t('Click to connect')}: ${pendingTool.name}`}
													on:click|preventDefault={() => {
														oauthRedirectHandler(pendingTool, chatInputDraft);
													}}
													type="button"
													class="flex h-[1.875rem] min-w-0 max-w-full translate-y-[0.0625rem] items-center self-center gap-1 bg-transparent px-1 text-[0.8125rem] leading-none text-gray-600 transition-colors duration-150 hover:text-gray-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gray-400 dark:text-gray-300 dark:hover:text-gray-100 dark:focus-visible:outline-gray-500"
												>
													<span class="truncate">{pendingTool.name}</span>
													<ArrowUpLeft className="size-3 shrink-0 rotate-90" strokeWidth="1.5" />
												</button>
											</Tooltip>
										{/each}
									</div>
								</div>

								<div class="self-end flex space-x-1 mr-1 min-w-0 gap-[0.03125rem]">
									<div class="flex min-w-0 max-w-[10rem] items-center sm:max-w-[13rem]">
										<ModelSelector
											bind:this={modelSelector}
											multipleEnabled={multipleModels}
											bind:selectedModels
											{onModelSelect}
											showSetDefault={!history?.currentId}
											placement="auto"
											align="end"
											triggerClassName="items-center gap-1.5 rounded-lg pl-2 pr-1.5 py-1 text-[0.8125rem] font-normal text-gray-600 transition-colors duration-100 hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-300 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
										/>
									</div>
									<ModelControls
										selectedModels={selectedModelIds}
										bind:chatFiles
										bind:params
										{onParamChange}
									/>

									{#if isActive && (history.messages[history.currentId]?.user_id ?? $_user?.id) === $_user?.id && prompt === '' && files.length === 0}
										<div class=" flex items-center">
											<Tooltip content={$i18n.t('Stop')}>
												<button
													aria-label={$i18n.t('Stop')}
													class="bg-white hover:bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-white dark:hover:bg-gray-800 transition rounded-full p-[0.3125rem]"
													on:click={() => {
														stopResponse();
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														viewBox="0 0 24 24"
														fill="currentColor"
														class="size-5"
													>
														<path
															fill-rule="evenodd"
															d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm6-2.438c0-.724.588-1.312 1.313-1.312h4.874c.725 0 1.313.588 1.313 1.313v4.874c0 .725-.588 1.313-1.313 1.313H9.564a1.312 1.312 0 01-1.313-1.313V9.564z"
															clip-rule="evenodd"
														/>
													</svg>
												</button>
											</Tooltip>
										</div>
									{:else}
										{#if !history?.currentId || history.messages[history.currentId]?.done == true}
											{#if $_user?.role === 'admin' || ($_user?.permissions?.chat?.stt ?? true)}
												<!-- {$i18n.t('Record voice')} -->
												<Tooltip content={$i18n.t('Dictate')}>
													<button
														id={inputId === 'chat-input'
															? 'voice-input-button'
															: `${inputId}-voice-input-button`}
														class=" text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 transition rounded-full p-1.5 self-center mr-0.5"
														type="button"
														on:click={async () => {
															try {
																let stream = await navigator.mediaDevices
																	.getUserMedia({ audio: true })
																	.catch(function (err) {
																		toast.error(
																			$i18n.t(
																				`Permission denied when accessing microphone: {{error}}`,
																				{
																					error: err
																				}
																			)
																		);
																		return null;
																	});

																if (stream) {
																	recording = true;
																	const tracks = stream.getTracks();
																	tracks.forEach((track) => track.stop());
																}
																stream = null;
															} catch {
																toast.error($i18n.t('Permission denied when accessing microphone'));
															}
														}}
														aria-label={$i18n.t('Voice Input')}
													>
														<Mic className="size-[1.125rem]" />
													</button>
												</Tooltip>
											{/if}
										{/if}

										{#if callEnabled && !embedded && prompt === '' && files.length === 0 && showCallButton}
											<div class=" flex items-center">
												<!-- {$i18n.t('Call')} -->
												<Tooltip
													content={callActive ? $i18n.t('Call in progress') : $i18n.t('Voice mode')}
												>
													<button
														class=" bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full p-[0.3125rem] self-center"
														class:call-active={callActive}
														type="button"
														on:click={async () => {
															if (callActive) {
																showCallOverlay.set(true);
																showTabs.set(true);
																return;
															}
															if (selectedModels.length > 1) {
																toast.error($i18n.t('Select only one model to call'));

																return;
															}

															if ($config?.audio?.realtime?.enabled) {
																showCallOverlay.set(true);
																showTabs.set(true);
																return;
															}

															// check if user has access to getUserMedia
															try {
																let stream = await navigator.mediaDevices.getUserMedia({
																	audio: true
																});
																// If the user grants the permission, proceed to show the call overlay

																if (stream) {
																	const tracks = stream.getTracks();
																	tracks.forEach((track) => track.stop());
																}

																stream = null;

																if ($settings.audio?.tts?.engine === 'browser-kokoro') {
																	// If the user has not initialized the TTS worker, initialize it
																	if (!$TTSWorker) {
																		await TTSWorker.set(
																			new KokoroWorker({
																				dtype: $settings.audio?.tts?.engineConfig?.dtype ?? 'fp32'
																			})
																		);

																		await $TTSWorker.init();
																	}
																}

																showCallOverlay.set(true);
																showTabs.set(true);
															} catch (err) {
																// If the user denies the permission or an error occurs, show an error message
																toast.error(
																	$i18n.t('Permission denied when accessing media devices')
																);
															}
														}}
														aria-label={callActive
															? $i18n.t('Return to call')
															: $i18n.t('Voice mode')}
													>
														<Voice className="size-5" strokeWidth="2.5" />
													</button>
												</Tooltip>
											</div>
										{:else}
											<div class=" flex items-center">
												<Tooltip
													content={isProcessingInputFiles
														? $i18n.t('Waiting for upload...')
														: $i18n.t('Send message')}
												>
													<button
														id={inputId === 'chat-input'
															? 'send-message-button'
															: `${inputId}-send-message-button`}
														aria-label={$i18n.t('Send message')}
														class="{!(prompt === '' && files.length === 0) &&
														!isProcessingInputFiles
															? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 '
															: 'text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700 disabled'} transition rounded-full p-[0.3125rem] self-center"
														type="submit"
														disabled={isProcessingInputFiles ||
															(prompt === '' && files.length === 0)}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 16 16"
															fill="currentColor"
															class="size-5"
														>
															<path
																fill-rule="evenodd"
																d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
																clip-rule="evenodd"
															/>
														</svg>
													</button>
												</Tooltip>
											</div>
										{/if}
									{/if}
								</div>
							</div>
						</div>

						{#if $config?.license_metadata?.input_footer}
							<div class=" text-xs text-gray-500 text-center line-clamp-1 marked">
								{@html DOMPurify.sanitize(marked($config?.license_metadata?.input_footer))}
							</div>
						{:else}
							<div class="mb-0.5"></div>
						{/if}
					</form>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.call-active {
		box-shadow:
			0 0 8px 2px rgb(0 0 0 / 0.25),
			0 0 20px 4px rgb(0 0 0 / 0.15);
	}

	:global(.dark) .call-active {
		box-shadow:
			0 0 8px 2px rgb(255 255 255 / 0.4),
			0 0 20px 4px rgb(255 255 255 / 0.25);
	}
</style>
