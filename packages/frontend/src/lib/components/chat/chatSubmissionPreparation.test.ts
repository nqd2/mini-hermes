import { describe, expect, it, vi } from 'vitest';
import {
	prepareChatSubmission,
	type ChatSubmissionPreparationOptions,
} from './chatSubmissionPreparation';

describe('prepareChatSubmission', () => {
	const createOptions = (
		overrides: Partial<ChatSubmissionPreparationOptions<{ id: string }>> = {},
	) => {
		const history = { id: 'history-1' };
		const isCurrentHistory = vi.fn(() => true);
		const resolveChatId = vi.fn(async () => 'chat-1');
		const ensureChatId = vi.fn(async () => 'chat-1');
		const hasPendingFiles = vi.fn(() => false);
		const uploadPendingFiles = vi.fn(async () => true);

		return {
			history,
			isCurrentHistory,
			resolveChatId,
			ensureChatId,
			hasPendingFiles,
			uploadPendingFiles,
			...overrides,
		};
	};

	it('stops before resolving a chat ID when the conversation is already stale', async () => {
		const options = createOptions({ isCurrentHistory: vi.fn(() => false) });

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'stale',
		});
		expect(options.resolveChatId).not.toHaveBeenCalled();
	});

	it('does not upload when chat ID resolution fails', async () => {
		const options = createOptions({
			resolveChatId: vi.fn(async () => null),
			hasPendingFiles: vi.fn(() => true),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'missing-chat',
		});
		expect(options.uploadPendingFiles).not.toHaveBeenCalled();
	});

	it('uploads pending files for the resolved chat and returns ready', async () => {
		const options = createOptions({ hasPendingFiles: vi.fn(() => true) });

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'ready',
		});
		expect(options.uploadPendingFiles).toHaveBeenCalledWith('chat-1');
		expect(options.ensureChatId).not.toHaveBeenCalled();
	});

	it('rechecks history after resolving the chat before uploading', async () => {
		const options = createOptions({
			isCurrentHistory: vi
				.fn()
				.mockReturnValueOnce(true)
				.mockReturnValueOnce(false),
			hasPendingFiles: vi.fn(() => true),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'stale',
		});
		expect(options.uploadPendingFiles).not.toHaveBeenCalled();
	});

	it('initializes a chat ID before uploading when the resolved ID is empty', async () => {
		const options = createOptions({
			resolveChatId: vi.fn(async () => ''),
			hasPendingFiles: vi.fn(() => true),
			ensureChatId: vi.fn(async () => 'created-chat'),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'ready',
		});
		expect(options.ensureChatId).toHaveBeenCalledWith(options.history);
		expect(options.uploadPendingFiles).toHaveBeenCalledWith('created-chat');
	});

	it('does not upload if the conversation changes while creating the fallback chat', async () => {
		const options = createOptions({
			isCurrentHistory: vi
				.fn()
				.mockReturnValueOnce(true)
				.mockReturnValueOnce(true)
				.mockReturnValueOnce(false),
			resolveChatId: vi.fn(async () => ''),
			hasPendingFiles: vi.fn(() => true),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'stale',
		});
		expect(options.ensureChatId).toHaveBeenCalledWith(options.history);
		expect(options.uploadPendingFiles).not.toHaveBeenCalled();
	});

	it('returns upload-failed when pending files cannot be uploaded', async () => {
		const options = createOptions({
			hasPendingFiles: vi.fn(() => true),
			uploadPendingFiles: vi.fn(async () => false),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'upload-failed',
		});
	});

	it('rechecks history after upload finishes', async () => {
		const options = createOptions({
			isCurrentHistory: vi
				.fn()
				.mockReturnValueOnce(true)
				.mockReturnValueOnce(true)
				.mockReturnValueOnce(false),
			hasPendingFiles: vi.fn(() => true),
		});

		await expect(prepareChatSubmission(options)).resolves.toEqual({
			status: 'stale',
		});
	});
});
