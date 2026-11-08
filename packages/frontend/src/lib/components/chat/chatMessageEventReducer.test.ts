import { describe, expect, it } from 'vitest';
import { reduceChatMessageEvent } from './chatMessageEventReducer';

describe('reduceChatMessageEvent', () => {
	it('appends message deltas without mutating the previous message', () => {
		const message = { content: 'Hello', statusHistory: [] };

		const result = reduceChatMessageEvent(message, 'chat:message:delta', { content: ' world' });

		expect(result.handled).toBe(true);
		expect(result.message.content).toBe('Hello world');
		expect(message.content).toBe('Hello');
	});

	it('appends files to temporary messages and replaces them for persisted messages', () => {
		const message = { files: [{ id: 'existing' }] };
		const newFiles = [{ id: 'new' }];

		const temporary = reduceChatMessageEvent(
			message,
			'chat:message:files',
			{ files: newFiles },
			{ temporaryChat: true }
		);
		const persisted = reduceChatMessageEvent(message, 'files', { files: newFiles });

		expect(temporary.message.files).toEqual([{ id: 'existing' }, { id: 'new' }]);
		expect(persisted.message.files).toEqual(newFiles);
	});

	it('returns task and embed updates as effects while preserving message fields', () => {
		const message = { content: 'Answer' };
		const tasks = [{ id: 'task-1' }];
		const taskResult = reduceChatMessageEvent(message, 'chat:message:tasks', { tasks });
		const embedResult = reduceChatMessageEvent(message, 'chat:message:embeds', {
			embeds: [{ id: 'embed-1' }]
		});

		expect(taskResult.tasks).toBe(tasks);
		expect(taskResult.message).toBe(message);
		expect(embedResult.message.embeds).toEqual([{ id: 'embed-1' }]);
		expect(embedResult.scrollToEmbeds).toBe(true);
	});

	it('leaves events outside its responsibility untouched', () => {
		const message = { content: 'Answer' };

		const result = reduceChatMessageEvent(message, 'chat:completion', {});

		expect(result).toEqual({ handled: false, message });
	});
});
