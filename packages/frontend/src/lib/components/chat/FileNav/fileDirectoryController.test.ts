import { describe, expect, expectTypeOf, it, vi } from 'vitest';
import type { TerminalFileList } from '$lib/apis/terminal';
import { createFileNavRequestLifecycle } from './fileNavRequestLifecycle';
import { createFileDirectoryController } from './fileDirectoryController';

const terminal = { url: 'https://terminal.example', key: 'secret' };
const directory = {
	entries: [{ name: 'report.xlsx', type: 'file' as const }],
	writable: false
};

const deferred = <T>() => {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
};

type Dependencies = Parameters<typeof createFileDirectoryController>[0];
type CurrentLoadOutcome = Awaited<
	ReturnType<ReturnType<typeof createFileDirectoryController>['loadCurrent']>
>;
type CurrentLoadOutcomeContract =
	{ status: 'loaded'; result: TerminalFileList } | { status: 'failed' } | { status: 'stale' };

const createController = (overrides: Partial<Dependencies> = {}) => {
	const lifecycle = createFileNavRequestLifecycle();
	let scope = { terminalId: 'terminal-1', chatId: 'chat-1' };
	const listFiles = vi.fn(async () => directory);
	const setCwd = vi.fn();
	const onCurrentRequest = vi.fn();
	const dependencies: Dependencies = {
		lifecycle,
		getScope: () => scope,
		listFiles,
		setCwd,
		onCurrentRequest,
		...overrides
	};
	const controller = createFileDirectoryController(dependencies);
	return {
		controller,
		lifecycle,
		listFiles: dependencies.listFiles,
		setCwd,
		onCurrentRequest,
		setScope(next: typeof scope) {
			scope = next;
		}
	};
};

describe('file directory controller', () => {
	it('exposes loaded, failed and stale as distinct outcomes', () => {
		expectTypeOf<CurrentLoadOutcome>().toEqualTypeOf<CurrentLoadOutcomeContract>();
	});

	it('loads the current directory, scopes the request and updates terminal cwd', async () => {
		const { controller, listFiles, setCwd, onCurrentRequest } = createController();
		const request = controller.beginCurrentRequest();

		const result = await controller.loadCurrent(request, {
			terminal,
			path: '/workspace/',
			sessionId: 'chat-1',
			initialFile: false
		});

		expect(listFiles).toHaveBeenCalledWith(terminal.url, terminal.key, '/workspace/', 'chat-1');
		expect(onCurrentRequest).toHaveBeenCalledWith(request);
		expect(result).toEqual({ status: 'loaded', result: directory });
		expect(setCwd).toHaveBeenCalledWith(terminal.url, terminal.key, '/workspace/', 'chat-1');
	});

	it('does not update terminal cwd when loading an initial file', async () => {
		const { controller, setCwd } = createController();
		const request = controller.beginCurrentRequest();

		const result = await controller.loadCurrent(request, {
			terminal,
			path: '/workspace/',
			sessionId: 'chat-1',
			initialFile: true
		});

		expect(result).toEqual({ status: 'loaded', result: directory });
		expect(setCwd).not.toHaveBeenCalled();
	});

	it('ignores a late current-directory result after the terminal scope changes', async () => {
		const pending = deferred<typeof directory | null>();
		const { controller, listFiles, setCwd, setScope } = createController({
			listFiles: vi.fn(() => pending.promise)
		});
		const request = controller.beginCurrentRequest();
		const result = controller.loadCurrent(request, {
			terminal,
			path: '/workspace/',
			sessionId: 'chat-1',
			initialFile: false
		});

		setScope({ terminalId: 'terminal-2', chatId: 'chat-2' });
		controller.beginCurrentRequest();
		pending.resolve(directory);

		expect(await result).toEqual({ status: 'stale' });
		expect(listFiles).toHaveBeenCalledTimes(1);
		expect(setCwd).not.toHaveBeenCalled();
	});

	it('keeps independent expanded-directory requests scoped and clears each loading state', async () => {
		const pendingFirst = deferred<typeof directory | null>();
		const pendingSecond = deferred<typeof directory | null>();
		const listFiles = vi.fn((_url, _key, path) =>
			path.includes('first') ? pendingFirst.promise : pendingSecond.promise
		);
		const loadingChanges: Array<[string, boolean]> = [];
		const { controller } = createController({
			listFiles,
			onExpandedLoading: (path: string, loading: boolean) => loadingChanges.push([path, loading])
		});

		const first = controller.loadExpanded('/workspace/first/', terminal, 'chat-1');
		const second = controller.loadExpanded('/workspace/second/', terminal, 'chat-1');
		pendingSecond.resolve(directory);
		pendingFirst.resolve({ entries: [], writable: true });

		expect(await first).toEqual({
			status: 'loaded',
			result: { entries: [], writable: true }
		});
		expect(await second).toEqual({ status: 'loaded', result: directory });
		expect(loadingChanges).toEqual([
			['/workspace/first/', true],
			['/workspace/second/', true],
			['/workspace/second/', false],
			['/workspace/first/', false]
		]);
	});

	it('clears expanded loading after a failed request', async () => {
		const loadingChanges: boolean[] = [];
		const { controller } = createController({
			listFiles: vi.fn(async () => null),
			onExpandedLoading: (_path: string, loading: boolean) => loadingChanges.push(loading)
		});

		expect(await controller.loadExpanded('/workspace/missing/', terminal, 'chat-1')).toEqual({
			status: 'failed'
		});
		expect(loadingChanges).toEqual([true, false]);
	});
});
