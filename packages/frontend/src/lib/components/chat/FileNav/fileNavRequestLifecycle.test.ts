import { describe, expect, it } from 'vitest';
import { createFileNavRequestLifecycle } from './fileNavRequestLifecycle';

describe('createFileNavRequestLifecycle', () => {
	it('tracks expanded directories independently and rejects stale responses per directory', () => {
		const lifecycle = createFileNavRequestLifecycle();
		const oldDocs = lifecycle.beginScoped('dir:/docs/', 'terminal-a', 'chat-a');
		const nextImages = lifecycle.beginScoped('dir:/images/', 'terminal-a', 'chat-a');
		const nextDocs = lifecycle.beginScoped('dir:/docs/', 'terminal-a', 'chat-a');

		expect(lifecycle.isScopedCurrent(oldDocs, 'terminal-a', 'chat-a')).toBe(false);
		expect(lifecycle.isScopedCurrent(nextImages, 'terminal-a', 'chat-a')).toBe(true);
		expect(lifecycle.isScopedCurrent(nextDocs, 'terminal-a', 'chat-a')).toBe(true);
	});

	it('invalidates scoped requests when chat or terminal scope changes', () => {
		const lifecycle = createFileNavRequestLifecycle();
		const request = lifecycle.beginScoped('dir:/docs/', 'terminal-a', 'chat-a');

		expect(lifecycle.isScopedCurrent(request, 'terminal-b', 'chat-a')).toBe(false);
		expect(lifecycle.isScopedCurrent(request, 'terminal-a', 'chat-b')).toBe(false);
		lifecycle.invalidate();
		expect(lifecycle.isScopedCurrent(request, 'terminal-a', 'chat-a')).toBe(false);

		const next = lifecycle.beginScoped('dir:/docs/', 'terminal-a', 'chat-a');
		expect(lifecycle.isScopedCurrent(request, 'terminal-a', 'chat-a')).toBe(false);
		expect(lifecycle.isScopedCurrent(next, 'terminal-a', 'chat-a')).toBe(true);
	});

	it('allows only the newest request for the same terminal and chat', () => {
		const lifecycle = createFileNavRequestLifecycle();
		const previous = lifecycle.begin('terminal-a', 'chat-a');
		const current = lifecycle.begin('terminal-a', 'chat-a');

		expect(lifecycle.isCurrent(previous, 'terminal-a', 'chat-a')).toBe(false);
		expect(lifecycle.isCurrent(current, 'terminal-a', 'chat-a')).toBe(true);
		expect(lifecycle.version).toBe(current.generation);
	});

	it('rejects a response when the selected terminal or chat changed', () => {
		const lifecycle = createFileNavRequestLifecycle();
		const request = lifecycle.begin('terminal-a', 'chat-a');

		expect(lifecycle.isCurrent(request, 'terminal-b', 'chat-a')).toBe(false);
		expect(lifecycle.isCurrent(request, 'terminal-a', 'chat-b')).toBe(false);
	});

	it('invalidates pending requests when navigation is disposed or reset', () => {
		const lifecycle = createFileNavRequestLifecycle();
		const request = lifecycle.begin('terminal-a', 'chat-a');

		lifecycle.invalidate();

		expect(lifecycle.isCurrent(request, 'terminal-a', 'chat-a')).toBe(false);
		expect(lifecycle.version).toBe(request.generation + 1);
	});
});
