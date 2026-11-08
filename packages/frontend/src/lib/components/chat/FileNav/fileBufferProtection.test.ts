import { describe, expect, it, vi } from 'vitest';
import { createFileBufferProtection } from './fileBufferProtection';

describe('createFileBufferProtection', () => {
	type Dependencies = Parameters<typeof createFileBufferProtection>[0];
	const createProtection = (overrides: Partial<Dependencies> = {}) => {
		const hasUnsavedChanges = vi.fn(() => true);
		const isSaving = vi.fn(() => false);
		const hasPendingChoice = vi.fn(() => false);
		const requestChoice = vi.fn(async () => 'cancel' as const);
		const save = vi.fn(async () => {});
		const isDirty = vi.fn(() => false);
		const discard = vi.fn();
		const onSaveError = vi.fn();

		return {
			protection: createFileBufferProtection({
				hasUnsavedChanges,
				isSaving,
				hasPendingChoice,
				requestChoice,
				save,
				isDirty,
				discard,
				onSaveError,
				...overrides,
			}),
			hasUnsavedChanges,
			isSaving,
			hasPendingChoice,
			requestChoice,
			save,
			isDirty,
			discard,
			onSaveError,
		};
	};

	it('allows proceeding immediately when the buffer has no unsaved changes', async () => {
		const hasUnsavedChanges = vi.fn(() => false);
		const { protection, requestChoice } = createProtection({
			hasUnsavedChanges,
		});

		await expect(protection.protect()).resolves.toBe(true);
		expect(hasUnsavedChanges).toHaveBeenCalledOnce();
		expect(requestChoice).not.toHaveBeenCalled();
	});

	it('blocks while a save or another decision is already active', async () => {
		const saving = createProtection({ isSaving: vi.fn(() => true) });
		const choosing = createProtection({ hasPendingChoice: vi.fn(() => true) });

		await expect(saving.protection.protect()).resolves.toBe(false);
		await expect(choosing.protection.protect()).resolves.toBe(false);
		expect(saving.requestChoice).not.toHaveBeenCalled();
		expect(choosing.requestChoice).not.toHaveBeenCalled();
	});

	it('passes the sending intent to the user decision request', async () => {
		const { protection, requestChoice } = createProtection();

		await protection.protect(true);

		expect(requestChoice).toHaveBeenCalledWith(true);
	});

	it('does not proceed when the user cancels', async () => {
		const { protection, save, discard } = createProtection();

		await expect(protection.protect()).resolves.toBe(false);
		expect(save).not.toHaveBeenCalled();
		expect(discard).not.toHaveBeenCalled();
	});

	it('discards through the editor adapter and allows proceeding', async () => {
		const { protection, discard } = createProtection({
			requestChoice: vi.fn(async () => 'discard' as const),
		});

		await expect(protection.protect()).resolves.toBe(true);
		expect(discard).toHaveBeenCalledOnce();
	});

	it('allows proceeding after a successful save clears the dirty state', async () => {
		const { protection, save } = createProtection({
			requestChoice: vi.fn(async () => 'save' as const),
			isDirty: vi.fn(() => false),
		});

		await expect(protection.protect()).resolves.toBe(true);
		expect(save).toHaveBeenCalledOnce();
	});

	it('stays blocked when save completes but the editor remains dirty', async () => {
		const { protection } = createProtection({
			requestChoice: vi.fn(async () => 'save' as const),
			isDirty: vi.fn(() => true),
		});

		await expect(protection.protect()).resolves.toBe(false);
	});

	it('reports save failures and keeps the file open', async () => {
		const error = new Error('save failed');
		const { protection, onSaveError } = createProtection({
			requestChoice: vi.fn(async () => 'save' as const),
			save: vi.fn(async () => {
				throw error;
			}),
		});

		await expect(protection.protect()).resolves.toBe(false);
		expect(onSaveError).toHaveBeenCalledWith(error);
	});
});
