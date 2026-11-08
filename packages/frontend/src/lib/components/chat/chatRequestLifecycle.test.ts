import { describe, expect, it } from 'vitest';
import { createChatRequestLifecycle } from './chatRequestLifecycle';

describe('createChatRequestLifecycle', () => {
	it('aborts the previous request and returns a fresh signal when a load starts', () => {
		const lifecycle = createChatRequestLifecycle();
		const initialSignal = lifecycle.signal;

		const nextSignal = lifecycle.start();

		expect(initialSignal.aborted).toBe(true);
		expect(nextSignal.aborted).toBe(false);
		expect(nextSignal).not.toBe(initialSignal);
		expect(lifecycle.signal).toBe(nextSignal);
	});

	it('aborts the current request when the lifecycle is disposed', () => {
		const lifecycle = createChatRequestLifecycle();
		const signal = lifecycle.signal;

		lifecycle.abort();

		expect(signal.aborted).toBe(true);
	});
});
