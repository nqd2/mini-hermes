import { describe, expect, it, vi } from 'vitest';
import { createChatInteractionQueue, type ChatInteraction } from './chatInteractionQueue';
import { handleChatToolInteractionEvent } from './chatInteractionEventHandler';

const createHarness = (overrides = {}) => {
	const queue = createChatInteractionQueue<ChatInteraction>();
	const onInteractionDone = vi.fn();
	const handle = (event, callback, options = {}) =>
		handleChatToolInteractionEvent(event, callback, {
			currentChatId: 'chat-1',
			approvalMode: 'ask',
			isTemporaryChat: (chatId) => chatId?.startsWith('temp-') ?? false,
			queue,
			createId: () => 'generated-interaction',
			onInteractionDone,
			...overrides,
			...options
		});
	return { queue, onInteractionDone, handle };
};

const makeEvent = (type, data = {}, chatId = 'chat-1') => ({
	chat_id: chatId,
	message_id: 'message-1',
	data: { type, data }
});

describe('chat tool interaction event handler', () => {
	it('queues a current chat interaction with its server ID and callback', () => {
		const { queue, handle } = createHarness();
		const callback = vi.fn();

		expect(
			handle(
				makeEvent('request:user_input', { interaction_id: 'interaction-1', questions: ['Name?'] }),
				callback
			)
		).toBe(true);

		expect(queue.current).toEqual({
			id: 'interaction-1',
			messageId: 'message-1',
			type: 'request:user_input',
			data: { interaction_id: 'interaction-1', questions: ['Name?'] },
			callback
		});
		expect(callback).not.toHaveBeenCalled();
	});

	it('cancels an interaction from a chat that is no longer active', () => {
		const { queue, handle } = createHarness();
		const callback = vi.fn();

		handle(makeEvent('request:elicitation', {}, 'chat-2'), callback);

		expect(queue.interactions).toEqual([]);
		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith({ status: 'cancelled', answers: {} });
	});

	it('auto-approves tool confirmation for a temporary chat in full mode', () => {
		const { queue, handle } = createHarness({ approvalMode: 'full', currentChatId: 'temp-1' });
		const callback = vi.fn();

		handle(
			makeEvent(
				'confirmation',
				{ interaction_id: 'tool-1', tool_call: { name: 'read_file' } },
				'temp-1'
			),
			callback
		);

		expect(queue.interactions).toEqual([]);
		expect(callback).toHaveBeenCalledOnce();
		expect(callback).toHaveBeenCalledWith(true);
	});

	it('removes completed interaction without resolving its callback', () => {
		const { queue, handle, onInteractionDone } = createHarness();
		const callback = vi.fn();
		queue.enqueue({
			id: 'interaction-1',
			messageId: 'message-1',
			type: 'request:user_input',
			data: {},
			callback
		});

		expect(
			handle(makeEvent('request:interaction:done', { interaction_id: 'interaction-1' }), undefined)
		).toBe(true);

		expect(queue.interactions).toEqual([]);
		expect(onInteractionDone).toHaveBeenCalledWith('interaction-1');
		expect(callback).not.toHaveBeenCalled();
	});

	it('consumes interaction requests without callbacks and leaves other events to the chat handler', () => {
		const { queue, handle } = createHarness();

		expect(handle(makeEvent('request:user_input'), undefined)).toBe(true);
		expect(handle(makeEvent('chat:message:delta'), undefined)).toBe(false);
		expect(queue.interactions).toEqual([]);
	});

	it('generates an ID only when the server did not provide one', () => {
		const { queue, handle } = createHarness();

		handle(makeEvent('request:user_input'), vi.fn());

		expect(queue.current?.id).toBe('generated-interaction');
	});
});
