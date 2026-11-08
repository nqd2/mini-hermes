import { describe, expect, it, vi } from 'vitest';
import { createFileNavigationHistory, navigateFileHistoryEntry } from './fileNavigationHistory';

describe('file navigation history', () => {
	it('pushes distinct locations and ignores the active location', () => {
		const history = createFileNavigationHistory();

		history.push('/docs');
		history.push('/docs');
		history.push('/docs', '/docs/report.xlsx');

		expect(history.snapshot()).toEqual({
			entries: [
				{ path: '/docs', file: null },
				{ path: '/docs', file: '/docs/report.xlsx' }
			],
			index: 1
		});
	});

	it('truncates forward entries after navigating to a new location', () => {
		const history = createFileNavigationHistory();
		history.push('/one');
		history.push('/two');
		history.back();
		history.push('/three');

		expect(history.snapshot()).toEqual({
			entries: [
				{ path: '/one', file: null },
				{ path: '/three', file: null }
			],
			index: 1
		});
		expect(history.forward()).toBeNull();
	});

	it('returns adjacent entries and leaves the index unchanged at the boundaries', () => {
		const history = createFileNavigationHistory();
		history.push('/one');
		history.push('/two');

		expect(history.back()).toEqual({ path: '/one', file: null });
		expect(history.back()).toBeNull();
		expect(history.forward()).toEqual({ path: '/two', file: null });
		expect(history.forward()).toBeNull();
	});

	it('does not record a location when history recording is suppressed', () => {
		const history = createFileNavigationHistory();
		history.push('/one');
		history.push('/two', null, true);

		expect(history.snapshot()).toEqual({ entries: [{ path: '/one', file: null }], index: 0 });
	});
});

describe('file navigation execution', () => {
	it('always releases the busy state when loading fails', async () => {
		const busy: boolean[] = [];
		const loadDirectory = vi.fn().mockRejectedValue(new Error('network error'));
		const openFile = vi.fn();

		await expect(
			navigateFileHistoryEntry(
				{ path: '/docs', file: '/docs/report.xlsx' },
				{ setBusy: (value) => busy.push(value), loadDirectory, openFile }
			)
		).rejects.toThrow('network error');

		expect(busy).toEqual([true, false]);
		expect(openFile).not.toHaveBeenCalled();
	});
});
