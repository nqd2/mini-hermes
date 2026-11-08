import { describe, expect, it } from 'vitest';
import type { FileEntry } from '$lib/apis/terminal';
import {
	asDirectoryPath,
	buildVisibleRows,
	joinPath,
	normalizeEntries,
	normalizePath,
	sortEntries,
	withRowIndexes
} from './fileBrowserModel';

describe('file browser model', () => {
	it('normalizes Windows paths and treats entry names as single path segments', () => {
		expect(normalizePath('C:\\work\\\\reports\\')).toBe('C:/work/reports/');
		expect(asDirectoryPath('/workspace/src')).toBe('/workspace/src/');
		expect(joinPath('/workspace/', 'nested\\report.xlsx')).toBe('/workspace/report.xlsx');
		expect(normalizeEntries([{ name: 'nested/report.xlsx', type: 'file' }])).toEqual([
			{ name: 'report.xlsx', type: 'file' }
		]);
	});

	it('keeps directories first and sorts entries without mutating input', () => {
		const entries: FileEntry[] = [
			{ name: 'zeta.txt', type: 'file' },
			{ name: 'src', type: 'directory' },
			{ name: 'alpha.txt', type: 'file' }
		];

		expect(sortEntries(entries, 'name', true).map(({ name }) => name)).toEqual([
			'src',
			'alpha.txt',
			'zeta.txt'
		]);
		expect(entries.map(({ name }) => name)).toEqual(['zeta.txt', 'src', 'alpha.txt']);
	});

	it('builds visible tree rows from expanded-directory cache with stable indexes', () => {
		const entries: FileEntry[] = [
			{ name: 'src', type: 'directory' },
			{ name: '.secret', type: 'file' },
			{ name: 'readme.md', type: 'file' }
		];
		const rows = withRowIndexes(
			buildVisibleRows(
				entries,
				'/workspace/',
				new Set(['/workspace/src/']),
				new Map([['/workspace/src/', [{ name: 'main.ts', type: 'file' }]]]),
				false,
				'name',
				true
			)
		);

		expect(
			rows.map(({ name, fullPath, depth, rowIndex }) => ({ name, fullPath, depth, rowIndex }))
		).toEqual([
			{ name: 'src', fullPath: '/workspace/src/', depth: 0, rowIndex: 0 },
			{ name: 'main.ts', fullPath: '/workspace/src/main.ts', depth: 1, rowIndex: 1 },
			{ name: 'readme.md', fullPath: '/workspace/readme.md', depth: 0, rowIndex: 2 }
		]);
	});
});
