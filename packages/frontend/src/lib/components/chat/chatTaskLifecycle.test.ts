import { describe, expect, it } from 'vitest';
import { createChatTaskLifecycle } from './chatTaskLifecycle';

describe('createChatTaskLifecycle', () => {
	it('clears chat-scoped state and rejects stale task updates after switching chats', () => {
		const lifecycle = createChatTaskLifecycle();
		const firstChat = lifecycle.begin('chat-a');
		lifecycle.setTaskIds(['task-a'], firstChat);
		lifecycle.setTasks([{ id: 'task-a' }]);

		const secondChat = lifecycle.begin('chat-b');

		expect(lifecycle.state).toEqual({ chatId: 'chat-b', taskIds: null, tasks: [] });
		expect(lifecycle.setTaskIds(['stale-task'], firstChat)).toBe(false);
		expect(lifecycle.isCurrent(secondChat)).toBe(true);
	});

	it('normalizes empty task lists and preserves task ids when appending new tasks', () => {
		const lifecycle = createChatTaskLifecycle();
		lifecycle.begin('chat-a');
		lifecycle.setTaskIds(['task-a']);
		lifecycle.appendTaskIds(['task-b']);

		expect(lifecycle.state.taskIds).toEqual(['task-a', 'task-b']);
		lifecycle.setTaskIds([]);
		expect(lifecycle.state.taskIds).toBeNull();
	});

	it('does not overwrite task ids added while a chat task snapshot is loading', () => {
		const lifecycle = createChatTaskLifecycle();
		const load = lifecycle.begin('chat-a');
		lifecycle.appendTaskIds(['live-task']);

		expect(lifecycle.setTaskIds(['stale-snapshot'], load)).toBe(false);
		expect(lifecycle.state.taskIds).toEqual(['live-task']);
	});

	it('notifies subscribers with immutable snapshots', () => {
		const lifecycle = createChatTaskLifecycle();
		const snapshots = [];
		const unsubscribe = lifecycle.subscribe((state) => snapshots.push(state));

		lifecycle.begin('chat-a');
		lifecycle.setTaskIds(['task-a']);
		unsubscribe();
		lifecycle.setTaskIds(['task-b']);

		expect(snapshots).toHaveLength(3);
		expect(snapshots[2].taskIds).toEqual(['task-a']);
		expect(snapshots[1]).not.toBe(snapshots[2]);
	});
});
