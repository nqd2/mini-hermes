export type FileBufferChoice = 'save' | 'discard' | 'cancel';

type Dependencies = {
	hasUnsavedChanges: () => boolean;
	isSaving: () => boolean;
	hasPendingChoice: () => boolean;
	requestChoice: (sending: boolean) => Promise<FileBufferChoice>;
	save: () => Promise<void>;
	isDirty: () => boolean;
	discard: () => void;
	onSaveError: (error: unknown) => void;
};

export const createFileBufferProtection = ({
	hasUnsavedChanges,
	isSaving,
	hasPendingChoice,
	requestChoice,
	save,
	isDirty,
	discard,
	onSaveError,
}: Dependencies) => ({
	async protect(sending = false): Promise<boolean> {
		if (!hasUnsavedChanges()) return true;
		if (isSaving() || hasPendingChoice()) return false;

		const choice = await requestChoice(sending);
		if (choice === 'cancel') return false;
		if (choice === 'save') {
			try {
				await save();
			} catch (error) {
				onSaveError(error);
				return false;
			}
			if (isDirty()) return false;
		}
		if (choice === 'discard') discard();
		return true;
	},
});
