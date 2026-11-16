<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { fly } from 'svelte/transition';

	import { config, user, tools as _tools, mobile, knowledge, visibleControls } from '$lib/stores';
	import { getKnowledgeBases } from '$lib/apis/knowledge';

	import { createPicker } from '$lib/utils/google-drive-picker';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentArrowUp from '$lib/components/icons/DocumentArrowUp.svelte';
	import Camera from '$lib/components/icons/Camera.svelte';
	import Note from '$lib/components/icons/Note.svelte';
	import Clip from '$lib/components/icons/Clip.svelte';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
	import Refresh from '$lib/components/icons/Refresh.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import Chats from './InputMenu/Chats.svelte';
	import Files from './InputMenu/Files.svelte';
	import PersonalFiles from './InputMenu/PersonalFiles.svelte';
	import Knowledge from './InputMenu/Knowledge.svelte';
	import AttachWebpageModal from './AttachWebpageModal.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';

	const i18n: any = getContext('i18n');

	export let files = [];
	export let chatId = '';

	export let selectedModels: string[] = [];
	export let fileUploadCapableModels: string[] = [];
	export let visionCapableModels: string[] = [];

	export let screenCaptureHandler: Function;
	export let uploadFilesHandler: Function;
	export let inputFilesHandler: Function;

	export let uploadGoogleDriveHandler: Function;

	export let onUpload: Function;
	export let onClose: Function;
	export let onCompare: () => void = () => {};
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: Function = () => {};

	let show = false;
	let tab = '';

	let showAttachWebpageModal = false;
	export function openPersonalFiles() {
		tab = 'personal_files';
		show = true;
	}
	$: toolApprovalModes = [
		{
			value: 'full',
			label: $i18n.t('Full access'),
			description: $i18n.t('Run tools without asking for approval.')
		},
		{
			value: 'ask',
			label: $i18n.t('Ask for approval'),
			description: $i18n.t('Stop before each tool call until you allow or deny it.')
		}
	];

	let fileUploadEnabled = true;
	$: fileUploadEnabled =
		fileUploadCapableModels.length === selectedModels.length &&
		($user?.role === 'admin' || $user?.permissions?.chat?.file_upload);

	let webUploadEnabled = true;
	$: webUploadEnabled = $user?.role === 'admin' || ($user?.permissions?.chat?.web_upload ?? true);
	$: toolPermissionsEnabled = $config?.features?.enable_tool_permissions ?? false;

	$: if (!fileUploadEnabled && files.some((file) => file.type !== 'text')) {
		files = files.filter((file) => file.type === 'text');
	}

	const detectMobile = () => {
		const userAgent = navigator.userAgent || navigator.vendor || window.opera;
		return /android|iphone|ipad|ipod|windows phone/i.test(userAgent);
	};

	const handleFileChange = (event) => {
		const inputFiles = Array.from(event.target?.files);
		if (inputFiles && inputFiles.length > 0) {
			console.log(inputFiles);
			inputFilesHandler(inputFiles);
		}
	};

	const onSelect = (item) => {
		if (files.find((f) => f.id === item.id)) {
			return;
		}
		files = [
			...files,
			{
				...item,
				status: 'processed'
			}
		];

		show = false;
	};
</script>

<AttachWebpageModal
	bind:show={showAttachWebpageModal}
	onSubmit={(e) => {
		onUpload(e);
	}}
/>

<!-- Hidden file input used to open the camera on mobile -->
<input
	id="camera-input"
	type="file"
	accept="image/*"
	capture="environment"
	on:change={handleFileChange}
	class="hidden"
/>

<Dropdown
	bind:show
	visualViewportAware
	on:change={(e) => {
		if (e.detail === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<DropdownMenu className="{tab === 'personal_files' ? 'w-[min(24rem,calc(100vw-2rem))] max-h-80' : 'w-70 max-h-72'} overflow-hidden transition">
			{#if tab === ''}
				<div
					class="max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin"
					in:fly={{ x: -20, duration: 150 }}
				>
					{#if toolPermissionsEnabled}
						<button
							role="menuitem"
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
							on:click={() => {
								tab = 'tool_permissions';
							}}
						>
							<svg
								class="size-3.5 shrink-0"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="1.5"
								stroke-linecap="round"
								stroke-linejoin="round"
							>
								<path d="M12 22C12 22 20 18 20 12V5L12 2L4 5V12C4 18 12 22 12 22Z" />
							</svg>

							<div class="flex items-center w-full justify-between min-w-0">
								<div class="line-clamp-1">
									{$i18n.t('Tool Permissions')}
								</div>

								<div class="flex items-center gap-2 min-w-0">
									<div class="text-xs text-gray-500 truncate">
										{$i18n.t(
											toolApprovalModes.find((mode) => mode.value === toolApprovalMode)?.label ??
												'Full access'
										)}
									</div>
									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</div>
						</button>

						<div class="h-px mx-1 my-1 bg-gray-100 dark:bg-gray-800"></div>
					{/if}

					<button
						role="menuitem"
						class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
						type="button"
						on:click={() => { tab = 'personal_files'; }}
					>
						<DocumentArrowUp />
						<span class="line-clamp-1">Personal Files (/files)</span>
					</button>

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							role="menuitem"
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							type="button"
							on:click={() => {
								if (fileUploadEnabled) {
									uploadFilesHandler();
									show = false;
								}
							}}
						>
							<Clip />

							<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
						</button>
					</Tooltip>

					{#if visionCapableModels.length > 0 && $visibleControls['chat.capture']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (fileUploadEnabled) {
										if (!detectMobile()) {
											screenCaptureHandler();
										} else {
											const cameraInputElement = document.getElementById('camera-input');

											if (cameraInputElement) {
												cameraInputElement.click();
											}
										}
										show = false;
									}
								}}
							>
								<Camera />
								<div class=" line-clamp-1">{$i18n.t('Capture')}</div>
							</button>
						</Tooltip>
					{/if}

					{#if $visibleControls['chat.compare']}
						<button
							role="menuitem"
							type="button"
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
							on:click={async () => {
								show = false;
								await tick();
								onCompare();
							}}
						>
							<svg
								class="size-3.5 shrink-0"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="1.5"
								stroke-linecap="round"
								stroke-linejoin="round"
								aria-hidden="true"
							>
								<rect x="3" y="4" width="18" height="16" rx="2" />
								<path d="M12 4v16M5.5 12h4M14.5 12h4M16.5 10v4" />
							</svg>
							<span>{$i18n.t('Compare text')}</span>
						</button>
					{/if}

					{#if $visibleControls['chat.attach_webpage']}
						<Tooltip
							content={!webUploadEnabled
								? $i18n.t('You do not have permission to upload web content.')
								: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!webUploadEnabled
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (webUploadEnabled) {
										showAttachWebpageModal = true;
										show = false;
									}
								}}
							>
								<GlobeAlt />
								<div class="line-clamp-1">{$i18n.t('Attach Webpage')}</div>
							</button>
						</Tooltip>
					{/if}

					{#if $visibleControls['chat.attach_files']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (fileUploadEnabled) {
										tab = 'files';
									}
								}}
							>
								<DocumentArrowUp />

								<div class="flex items-center w-full justify-between">
									<div class="line-clamp-1">
										{$i18n.t('Attach Files')}
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						</Tooltip>
					{/if}



					{#if $visibleControls['chat.attach_knowledge']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (fileUploadEnabled) {
										tab = 'knowledge';
									}
								}}
							>
								<Database />

								<div class="flex items-center w-full justify-between">
									<div class=" line-clamp-1">
										{$i18n.t('Attach Knowledge')}
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						</Tooltip>
					{/if}

					{#if $visibleControls['chat.reference_chats']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (fileUploadEnabled) {
										tab = 'chats';
									}
								}}
							>
								<ClockRotateRight />

								<div class="flex items-center w-full justify-between">
									<div class=" line-clamp-1">
										{$i18n.t('Reference Chats')}
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						</Tooltip>
					{/if}

					{#if fileUploadEnabled}
						{#if $config?.features?.enable_google_drive_integration}
							<button
								role="menuitem"
								class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
								type="button"
								on:click={() => {
									uploadGoogleDriveHandler();
									show = false;
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 87.3 78"
									class="size-3.5 shrink-0"
								>
									<path
										d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"
										fill="#0066da"
									/>
									<path
										d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"
										fill="#00ac47"
									/>
									<path
										d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z"
										fill="#ea4335"
									/>
									<path
										d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"
										fill="#00832d"
									/>
									<path
										d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"
										fill="#2684fc"
									/>
									<path
										d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"
										fill="#ffba00"
									/>
								</svg>
								<div class="line-clamp-1">{$i18n.t('Google Drive')}</div>
							</button>
						{/if}


					{/if}
				</div>
			{:else if tab === 'tool_permissions'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Tool Permissions')}
							</div>
						</div>
					</button>

					<div class="mt-1 space-y-1">
						{#each toolApprovalModes as mode}
							<Tooltip content={$i18n.t(mode.description)} className="w-full">
								<button
									role="menuitemradio"
									aria-checked={toolApprovalMode === mode.value}
									class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
									on:click={() => {
										toolApprovalMode = mode.value;
										onToolApprovalModeChange(mode.value);
										tab = '';
									}}
								>
									<div class="flex items-center w-full justify-between min-w-0">
										<div class="line-clamp-1">{$i18n.t(mode.label)}</div>
										{#if toolApprovalMode === mode.value}
											<svg
												class="size-3 shrink-0 text-gray-500"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="2.5"
												stroke-linecap="round"
												stroke-linejoin="round"
											>
												<polyline points="20 6 9 17 4 12" />
											</svg>
										{/if}
									</div>
								</button>
							</Tooltip>
						{/each}
					</div>
				</div>
			{:else if tab === 'knowledge'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Knowledge')}
							</div>
						</div>
					</button>

					<Knowledge {onSelect} />
				</div>
			{:else if tab === 'files'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Files')}
							</div>
						</div>
					</button>

					<Files {onSelect} />
				</div>
			{:else if tab === 'personal_files'}
				<div class="flex h-80 w-full min-w-0 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => { tab = ''; }}
					>
						<ChevronLeft />
						<span>Personal Files</span>
					</button>
					<PersonalFiles {chatId} {files} />
				</div>
			{:else if tab === 'chats'}
				<div class="flex max-h-72 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Chats')}
							</div>
						</div>
					</button>

					<Chats {onSelect} />
				</div>

			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
