import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	createFileMatchSearchController,
	type FileMatchSearchState
} from './fileMatchSearchController';

const query = {
	baseUrl: 'https://terminal.example',
	apiKey: 'secret',
	query: 'budget',
	path: '/workspace/',
	showHidden: false,
	sessionId: 'chat-1'
};

const match = (path: string) => ({
	path,
	relative_path: path,
	name: path,
	type: 'file' as const,
	name_match: true,
	content_matches: []
});

afterEach(() => {
	vi.useRealTimers();
});

describe('file match search controller', () => {
	it('debounces a search and publishes its results and next offset', async () => {
		vi.useFakeTimers();
		const search = vi.fn(async () => ({ results: [match('budget.xlsx')], next_offset: 20 }));
		let state: FileMatchSearchState | undefined;
		const controller = createFileMatchSearchController({
			search,
			onState: (next) => (state = next),
			errorMessage: () => 'Search failed',
			debounceMs: 150
		});

		controller.queue(query);
		expect(state?.loading).toBe(true);
		expect(search).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(150);

		expect(search).toHaveBeenCalledWith(query, 0, expect.any(AbortSignal));
		expect(state?.results).toEqual([match('budget.xlsx')]);
		expect(state?.nextOffset).toBe(20);
		expect(state?.loading).toBe(false);
	});

	it('appends paginated results without losing the new continuation offset', async () => {
		vi.useFakeTimers();
		const search = vi
			.fn()
			.mockResolvedValueOnce({ results: [match('first.xlsx')], next_offset: 10 })
			.mockResolvedValueOnce({ results: [match('second.xlsx')], next_offset: null });
		let state: FileMatchSearchState | undefined;
		const controller = createFileMatchSearchController({
			search,
			onState: (next) => (state = next),
			errorMessage: () => 'Search failed',
			debounceMs: 0
		});

		controller.queue(query);
		await vi.advanceTimersByTimeAsync(0);
		await controller.loadMore(true);

		expect(search).toHaveBeenNthCalledWith(1, query, 0, expect.any(AbortSignal));
		expect(search).toHaveBeenNthCalledWith(2, query, 10, expect.any(AbortSignal));
		expect(state?.results).toEqual([match('first.xlsx'), match('second.xlsx')]);
		expect(state?.nextOffset).toBeNull();
	});

	it('allows a failed page to be retried by the next explicit load-more action', async () => {
		vi.useFakeTimers();
		const search = vi
			.fn()
			.mockResolvedValueOnce({ results: [match('first.xlsx')], next_offset: 10 })
			.mockResolvedValueOnce(null)
			.mockResolvedValueOnce({ results: [match('second.xlsx')], next_offset: null });
		let state: FileMatchSearchState | undefined;
		const controller = createFileMatchSearchController({
			search,
			onState: (next) => (state = next),
			errorMessage: () => 'Search failed',
			debounceMs: 0
		});

		controller.queue(query);
		await vi.advanceTimersByTimeAsync(0);
		await controller.loadMore(true);
		expect(state?.loadMoreError).toBe(true);

		await controller.loadMore(true);

		expect(search).toHaveBeenCalledTimes(3);
		expect(state?.results).toEqual([match('first.xlsx'), match('second.xlsx')]);
		expect(state?.loadMoreError).toBe(false);
	});

	it('ignores an older response after a newer query replaces it', async () => {
		vi.useFakeTimers();
		let resolveOld:
			| ((value: { results: ReturnType<typeof match>[]; next_offset: number | null }) => void)
			| undefined;
		const oldResponse = new Promise<{
			results: ReturnType<typeof match>[];
			next_offset: number | null;
		}>((resolve) => (resolveOld = resolve));
		let oldSignal: AbortSignal | undefined;
		const search = vi
			.fn()
			.mockImplementationOnce((_request, _offset, signal) => {
				oldSignal = signal;
				return oldResponse;
			})
			.mockResolvedValueOnce({ results: [match('new.xlsx')], next_offset: null });
		let state: FileMatchSearchState | undefined;
		const controller = createFileMatchSearchController({
			search,
			onState: (next) => (state = next),
			errorMessage: () => 'Search failed',
			debounceMs: 0
		});

		controller.queue(query);
		await vi.advanceTimersByTimeAsync(0);
		controller.queue({ ...query, query: 'new query' });
		expect(oldSignal?.aborted).toBe(true);
		await vi.advanceTimersByTimeAsync(0);
		resolveOld?.({ results: [match('stale.xlsx')], next_offset: null });
		await Promise.resolve();

		expect(state?.results).toEqual([match('new.xlsx')]);
		expect(state?.loading).toBe(false);
	});

	it('clears a pending debounce timer on reset', async () => {
		vi.useFakeTimers();
		const search = vi.fn(async () => ({ results: [], next_offset: null }));
		let state: FileMatchSearchState | undefined;
		const controller = createFileMatchSearchController({
			search,
			onState: (next) => (state = next),
			errorMessage: () => 'Search failed',
			debounceMs: 150
		});

		controller.queue(query);
		controller.reset();
		await vi.advanceTimersByTimeAsync(200);

		expect(search).not.toHaveBeenCalled();
		expect(state).toMatchObject({
			results: null,
			loading: false,
			loadingMore: false,
			error: null,
			loadMoreError: false,
			nextOffset: null
		});
	});
});
