export type ToolApprovalMode = 'ask' | 'full';

type ToolApprovalModeHandlerDependencies = {
	setMode: (mode: ToolApprovalMode) => void;
	persistChatMode: (mode: ToolApprovalMode) => Promise<unknown>;
	persistUserSettings: () => Promise<unknown>;
	onSettingsError: (error: unknown) => void;
	onEnableFullMode: () => Promise<unknown>;
};

/** Coordinates approval-mode persistence before applying full-mode approvals. */
export const createToolApprovalModeHandler = ({
	setMode,
	persistChatMode,
	persistUserSettings,
	onSettingsError,
	onEnableFullMode
}: ToolApprovalModeHandlerDependencies) => {
	let requestVersion = 0;
	let pendingChange: Promise<void> = Promise.resolve();

	return (requestedMode: string) => {
		const mode: ToolApprovalMode = requestedMode === 'ask' ? 'ask' : 'full';
		const version = ++requestVersion;
		setMode(mode);

		const change = pendingChange.then(async () => {
			await persistChatMode(mode);
			if (version !== requestVersion) return;
			try {
				await persistUserSettings();
			} catch (error) {
				onSettingsError(error);
			}
			if (version === requestVersion && mode === 'full') await onEnableFullMode();
		});
		pendingChange = change.then(
			() => undefined,
			() => undefined
		);
		return change;
	};
};
