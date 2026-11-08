import { describe, expect, it } from 'vitest';
import { createFileInputLifecycle } from './fileInputLifecycle';

describe('createFileInputLifecycle', () => {
	it('tracks each file until every asynchronous handler settles', async () => {
		const stateChanges: number[] = [];
		const lifecycle = createFileInputLifecycle((pendingCount) => stateChanges.push(pendingCount));
		const resolvers: Array<() => void> = [];
		const batch = lifecycle.process(
			[1, 2],
			() => new Promise<void>((resolve) => resolvers.push(resolve))
		);

		expect(lifecycle.pendingCount).toBe(2);
		resolvers[0]();
		await Promise.resolve();
		expect(lifecycle.pendingCount).toBe(1);
		expect(lifecycle.isProcessing).toBe(true);

		resolvers[1]();
		const results = await batch;

		expect(results.map(({ status }) => status)).toEqual(['fulfilled', 'fulfilled']);
		expect(lifecycle.pendingCount).toBe(0);
		expect(lifecycle.isProcessing).toBe(false);
		expect(stateChanges).toEqual([1, 2, 1, 0]);
	});

	it('records a failed file without leaving the batch stuck', async () => {
		const lifecycle = createFileInputLifecycle();
		const results = await lifecycle.process([1, 2], async (item) => {
			if (item === 1) throw new Error('upload failed');
		});

		expect(results.map(({ status }) => status)).toEqual(['rejected', 'fulfilled']);
		expect(lifecycle.pendingCount).toBe(0);
		expect(lifecycle.isProcessing).toBe(false);
	});

	it('tracks upload operations started outside the input-file batch', async () => {
		const stateChanges: number[] = [];
		const lifecycle = createFileInputLifecycle((pendingCount) => stateChanges.push(pendingCount));
		let finishUpload: (value: string) => void = () => {};
		const upload = lifecycle.track(
			() => new Promise<string>((resolve) => (finishUpload = resolve))
		);

		expect(lifecycle.isProcessing).toBe(true);
		finishUpload('uploaded');

		await expect(upload).resolves.toBe('uploaded');
		expect(lifecycle.pendingCount).toBe(0);
		expect(stateChanges).toEqual([1, 0]);
	});
});
