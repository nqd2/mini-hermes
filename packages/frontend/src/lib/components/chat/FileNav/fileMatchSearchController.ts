import type { TerminalFileMatchesResponse } from '$lib/apis/terminal';

export type FileMatchSearchQuery = {
	baseUrl: string;
	apiKey: string;
	query: string;
	path: string;
	showHidden: boolean;
	sessionId?: string;
};

export type FileMatchSearchState = {
	results: TerminalFileMatchesResponse['results'] | null;
	loading: boolean;
	loadingMore: boolean;
	error: string | null;
	loadMoreError: boolean;
	nextOffset: number | null;
};

type Search = (
	query: FileMatchSearchQuery,
	offset: number,
	signal: AbortSignal
) => Promise<TerminalFileMatchesResponse | null>;

type Dependencies = {
	search: Search;
	onState: (state: FileMatchSearchState) => void;
	errorMessage: () => string;
	debounceMs?: number;
};

const emptyState = (): FileMatchSearchState => ({
	results: null,
	loading: false,
	loadingMore: false,
	error: null,
	loadMoreError: false,
	nextOffset: null
});

export const createFileMatchSearchController = ({
	search,
	onState,
	errorMessage,
	debounceMs = 200
}: Dependencies) => {
	let state = emptyState();
	let requestId = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let activeController: AbortController | null = null;
	let currentQuery: FileMatchSearchQuery | null = null;

	const publish = () =>
		onState({
			...state,
			results: state.results ? [...state.results] : null
		});

	const clearRequest = () => {
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		activeController?.abort();
		activeController = null;
		requestId += 1;
	};

	const reset = () => {
		clearRequest();
		currentQuery = null;
		state = emptyState();
		publish();
	};

	const queue = (query: FileMatchSearchQuery | null) => {
		clearRequest();
		if (!query || !query.query.trim()) {
			currentQuery = null;
			state = emptyState();
			publish();
			return;
		}

		const requestQuery = { ...query };
		currentQuery = requestQuery;
		state = {
			results: null,
			loading: true,
			loadingMore: false,
			error: null,
			loadMoreError: false,
			nextOffset: null
		};
		publish();

		const queuedRequestId = requestId;
		const controller = new AbortController();
		activeController = controller;
		timer = setTimeout(async () => {
			timer = null;
			const results = await search(requestQuery, 0, controller.signal).catch(() => null);
			if (queuedRequestId !== requestId) return;

			if (results) {
				state.results = results.results;
				state.nextOffset = results.next_offset;
			} else if (!controller.signal.aborted) {
				state.error = errorMessage();
				state.results = [];
			}
			state.loading = false;
			if (activeController === controller) activeController = null;
			publish();
		}, debounceMs);
	};

	const loadMore = async (enabled: boolean) => {
		const offset = state.nextOffset;
		const query = currentQuery;
		if (!enabled || !query || offset === null || state.loading || state.loadingMore) {
			return;
		}

		const activeRequestId = requestId;
		const controller = new AbortController();
		activeController?.abort();
		activeController = controller;
		state.loadingMore = true;
		state.loadMoreError = false;
		publish();

		const results = await search(query, offset, controller.signal).catch(() => null);
		if (activeRequestId !== requestId) return;

		if (results) {
			state.results = [...(state.results ?? []), ...results.results];
			state.nextOffset = results.next_offset;
		} else if (!controller.signal.aborted) {
			state.loadMoreError = true;
		}
		state.loadingMore = false;
		if (activeController === controller) activeController = null;
		publish();
	};

	return { queue, loadMore, reset, clearRequest };
};
