import { describe, expect, it, vi } from 'vitest';
import { createChatInteractionQueue, type ChatInteraction } from './chatInteractionQueue';

const interaction = (id: string, messageId: string, callback = vi.fn()): ChatInteraction => ({
	id,
	messageId,
	type: 'confirmation',
	data: { tool_call: { name: 'write_file' } },
	callback
});

describe('chat interaction queue', () => {
	it('keeps pending interactions in FIFO order', () => {
		const queue = createChatInteractionQueue();
		const first = interaction('first', 'message-1');
		const second = interaction('second', 'message-2');

		queue.enqueue(first);
		queue.enqueue(second);

		expect(queue.current).toBe(first);
		expect(queue.interactions).toEqual([first, second]);
	});

	it('removes before calling a resolved callback and ignores duplicate resolutions', () => {
		const queue = createChatInteractionQueue();
		const callback = vi.fn();
		const pending = interaction('confirm', 'message-1', callback);
		queue.enqueue(pending);

		expect(queue.resolve(pending, true)).toBe(true);
		expect(queue.resolve(pending, false)).toBe(false);
		expect(queue.interactions).toEqual([]);
		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith(true);
	});

	it('cancels only interactions owned by the specified message', () => {
		const queue = createChatInteractionQueue();
		const firstCallback = vi.fn();
		const secondCallback = vi.fn();
		const unrelatedCallback = vi.fn();
		const first = interaction('first', 'message-1', firstCallback);
		const second = interaction('second', 'message-1', secondCallback);
		const unrelated = interaction('third', 'message-2', unrelatedCallback);
		queue.enqueue(first);
		queue.enqueue(second);
		queue.enqueue(unrelated);

		queue.resolveForMessage('message-1', { status: 'cancelled', answers: {} });

		expect(queue.interactions).toEqual([unrelated]);
		expect(firstCallback).toHaveBeenCalledOnce();
		expect(secondCallback).toHaveBeenCalledOnce();
		expect(unrelatedCallback).not.toHaveBeenCalled();
	});

	it('removes completed interaction IDs without invoking callbacks', () => {
		const queue = createChatInteractionQueue();
		const callback = vi.fn();
		queue.enqueue(interaction('done', 'message-1', callback));

		expect(queue.remove('done')).toBe(true);
		expect(queue.remove('done')).toBe(false);
		expect(callback).not.toHaveBeenCalled();
		expect(queue.current).toBeUndefined();
	});
});
