import { describe, expect, it } from 'vitest';
import { selectAllFilePaths, toggleFileSelection } from './fileSelectionModel';

const rows = ['/a', '/b', '/c'].map((fullPath) => ({ fullPath }));

describe('file selection model', () => {
	it('toggles one path without mutating the previous selection', () => {
		const previous = new Set(['/a']);
		const result = toggleFileSelection(previous, rows, '/b', false, 0);

		expect([...result.selectedPaths]).toEqual(['/a', '/b']);
		expect([...previous]).toEqual(['/a']);
		expect(result.lastClickedIndex).toBe(1);
	});

	it('selects a visible range from the previous anchor', () => {
		const result = toggleFileSelection(new Set(['/a']), rows, '/c', true, 0);

		expect([...result.selectedPaths]).toEqual(['/a', '/b', '/c']);
		expect(result.lastClickedIndex).toBe(0);
	});

	it('removes an already selected path and safely ignores stale row indexes', () => {
		const removed = toggleFileSelection(new Set(['/a', '/b']), rows, '/b', true, 0);
		const stale = toggleFileSelection(removed.selectedPaths, rows, '/missing', false, 0);

		expect([...removed.selectedPaths]).toEqual(['/a']);
		expect([...stale.selectedPaths]).toEqual(['/a']);
		expect(stale.lastClickedIndex).toBe(0);
	});

	it('builds select all from the currently visible paths', () => {
		expect(selectAllFilePaths(rows.map((row) => row.fullPath))).toEqual(
			new Set(['/a', '/b', '/c'])
		);
	});
});
