import { afterEach, describe, expect, it, vi } from 'vitest';
import { createChatDraftController, prepareChatInputDraft } from './chatDraftController';

afterEach(() => {
	vi.useRealTimers();
});

describe('chat drafts', () => {
	it('keeps only restorable files and removes access grants from the saved input', () => {
		const draft = prepareChatInputDraft({
			prompt: 'hello',
			files: [
				{ type: 'file', id: 'saved', user: 'private', access_grants: ['grant'] },
				{ type: 'image', id: 'image' },
				{ type: 'file', file: new File(['local'], 'local.txt') }
			],
			selectedToolIds: ['tool']
		});
		expect(draft.files).toEqual([{ type: 'file', id: 'saved', user: undefined, access_grants: undefined }]);
		expect(draft.selectedToolIds).toEqual(['tool']);
	});

	it('debounces writes and cancels a pending write when the draft is cleared', async () => {
		vi.useFakeTimers();
		const saved = new Map<string, string>();
		const storage = {
			setItem: (key: string, value: string) => saved.set(key, value),
			removeItem: (key: string) => saved.delete(key)
		};
		const drafts = createChatDraftController(storage);
		await drafts.save({ prompt: 'first' }, 'chat-1');
		await drafts.save({ prompt: 'second' }, 'chat-1');
		await vi.advanceTimersByTimeAsync(500);
		expect(JSON.parse(saved.get('chat-input-chat-1') ?? '{}')).toEqual({ prompt: 'second' });
		await drafts.save({ prompt: 'stale' }, 'chat-1');
		await drafts.clear('chat-1');
		await vi.advanceTimersByTimeAsync(500);
		expect(saved.has('chat-input-chat-1')).toBe(false);
	});

	it('writes immediately for OAuth redirect and removes oversized drafts', async () => {
		const saved = new Map<string, string>();
		const drafts = createChatDraftController({
			setItem: (key, value) => saved.set(key, value),
			removeItem: (key) => saved.delete(key)
		});
		await drafts.save({ prompt: 'redirect' }, null, false);
		expect(JSON.parse(saved.get('chat-input') ?? '{}').prompt).toBe('redirect');
		await drafts.save({ prompt: 'x'.repeat(5000) }, null, false);
		expect(saved.has('chat-input')).toBe(false);
	});
});
