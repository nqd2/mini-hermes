import { describe, expect, it, vi } from 'vitest';
import { createToolApprovalModeHandler } from './chatToolApprovalMode';

const createHandler = () => {
	const events: string[] = [];
	const handler = createToolApprovalModeHandler({
		setMode: (mode) => events.push(`mode:${mode}`),
		persistChatMode: async (mode) => events.push(`chat:${mode}`),
		persistUserSettings: async () => events.push('user-settings'),
		onSettingsError: () => events.push('settings-error'),
		onEnableFullMode: async () => events.push('approve-pending')
	});
	return { events, handler };
};

describe('tool approval mode handler', () => {
	it('normalizes unknown modes to full and only persists the selected mode', async () => {
		const { events, handler } = createHandler();

		await handler('unknown');

		expect(events).toEqual(['mode:full', 'chat:full', 'user-settings', 'approve-pending']);
	});

	it('keeps approval in ask mode without auto-approving pending calls', async () => {
		const { events, handler } = createHandler();

		await handler('ask');

		expect(events).toEqual(['mode:ask', 'chat:ask', 'user-settings']);
	});

	it('logs user settings errors but continues the full-mode action', async () => {
		const events: string[] = [];
		const handler = createToolApprovalModeHandler({
			setMode: (mode) => events.push(`mode:${mode}`),
			persistChatMode: async (mode) => events.push(`chat:${mode}`),
			persistUserSettings: async () => {
				throw new Error('settings unavailable');
			},
			onSettingsError: vi.fn(() => events.push('settings-error')),
			onEnableFullMode: async () => events.push('approve-pending')
		});

		await handler('full');

		expect(events).toEqual(['mode:full', 'chat:full', 'settings-error', 'approve-pending']);
	});

	it('stops before user settings and approval when saving the chat mode fails', async () => {
		const events: string[] = [];
		const handler = createToolApprovalModeHandler({
			setMode: (mode) => events.push(`mode:${mode}`),
			persistChatMode: async () => {
				throw new Error('chat save failed');
			},
			persistUserSettings: async () => events.push('user-settings'),
			onSettingsError: () => events.push('settings-error'),
			onEnableFullMode: async () => events.push('approve-pending')
		});

		await expect(handler('full')).rejects.toThrow('chat save failed');
		expect(events).toEqual(['mode:full']);
	});

	it('does not apply a stale full-mode approval after ask mode is selected', async () => {
		const events: string[] = [];
		let currentMode = '';
		let releaseFullPersistence: () => void = () => {};
		const fullPersistence = new Promise<void>((resolve) => {
			releaseFullPersistence = resolve;
		});
		const handler = createToolApprovalModeHandler({
			setMode: (mode) => {
				currentMode = mode;
				events.push(`mode:${mode}`);
			},
			persistChatMode: async (mode) => {
				events.push(`chat:${mode}`);
				if (mode === 'full') await fullPersistence;
			},
			persistUserSettings: async () => events.push('user-settings'),
			onSettingsError: () => events.push('settings-error'),
			onEnableFullMode: async () => events.push(`approve-pending:${currentMode}`)
		});

		const fullChange = handler('full');
		const askChange = handler('ask');
		releaseFullPersistence();
		await Promise.all([fullChange, askChange]);

		expect(currentMode).toBe('ask');
		expect(events).not.toContain('approve-pending:ask');
		expect(events).not.toContain('approve-pending:full');
	});
});
