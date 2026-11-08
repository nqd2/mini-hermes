import { describe, expect, it, vi } from 'vitest';
import { createFileMutationController } from './fileMutationController';

describe('createFileMutationController', () => {
	type Dependencies = Parameters<typeof createFileMutationController>[0];
	const createController = (overrides: Partial<Dependencies> = {}) => {
		const getTerminal: Dependencies['getTerminal'] = vi.fn(() => ({
			url: 'https://terminal.test',
			key: 'token',
		}));
		const getSessionId: Dependencies['getSessionId'] = vi.fn(() => 'chat-1');
		const getCurrentPath: Dependencies['getCurrentPath'] = vi.fn(
			() => '/working/',
		);
		const canWrite: Dependencies['canWrite'] = vi.fn(() => true);
		const moveEntry: Dependencies['moveEntry'] = vi.fn(
			async (_url, _key, source, destination, _sessionId) => ({
				source,
				destination,
			}),
		);

		return {
			controller: createFileMutationController({
				getTerminal,
				getSessionId,
				getCurrentPath,
				canWrite,
				moveEntry,
				...overrides,
			}),
			getTerminal,
			getSessionId,
			getCurrentPath,
			canWrite,
			moveEntry,
		};
	};

	it('blocks move and rename when terminal access is unavailable or read-only', async () => {
		const missingTerminal = createController({
			getTerminal: vi.fn(() => null),
		});
		const readOnly = createController({ canWrite: vi.fn(() => false) });

		await expect(
			missingTerminal.controller.move('/src/a.txt', '/dst'),
		).resolves.toEqual({
			status: 'blocked',
		});
		await expect(
			readOnly.controller.rename('/src/a.txt', 'b.txt'),
		).resolves.toEqual({
			status: 'blocked',
		});
		expect(missingTerminal.moveEntry).not.toHaveBeenCalled();
		expect(readOnly.moveEntry).not.toHaveBeenCalled();
	});

	it('rejects empty names, no-op moves, and moving a directory into itself or a child', async () => {
		const { controller, moveEntry } = createController();

		await expect(
			controller.move('/src/folder/', '/src/folder'),
		).resolves.toEqual({
			status: 'invalid',
		});
		await expect(
			controller.move('/src/folder/', '/src/folder/nested'),
		).resolves.toEqual({
			status: 'invalid',
		});
		await expect(controller.rename('/src/file.txt', '')).resolves.toEqual({
			status: 'blocked',
		});
		await expect(
			controller.rename('/src/file.txt', 'file.txt'),
		).resolves.toEqual({
			status: 'invalid',
		});
		expect(moveEntry).not.toHaveBeenCalled();
	});

	it('normalizes a move and passes its chat scope to the terminal adapter', async () => {
		const { controller, moveEntry } = createController();

		await expect(
			controller.move('/src//report.csv/', '/archive/'),
		).resolves.toEqual({
			status: 'moved',
			source: '/src/report.csv',
			destination: '/archive/report.csv',
			name: 'report.csv',
		});
		expect(moveEntry).toHaveBeenCalledWith(
			'https://terminal.test',
			'token',
			'/src/report.csv',
			'/archive/report.csv',
			'chat-1',
		);
	});

	it('returns terminal errors without claiming that the move succeeded', async () => {
		const { controller } = createController({
			moveEntry: vi.fn(async () => ({ error: 'Destination already exists' })),
		});

		await expect(
			controller.move('/src/report.csv', '/archive'),
		).resolves.toEqual({
			status: 'failed',
			error: 'Destination already exists',
		});
	});

	it('renames within the existing parent directory', async () => {
		const { controller, moveEntry } = createController();

		await expect(
			controller.rename('/src/report.csv', 'summary.csv'),
		).resolves.toEqual({
			status: 'renamed',
			destination: '/src/summary.csv',
			name: 'summary.csv',
		});
		expect(moveEntry).toHaveBeenCalledWith(
			'https://terminal.test',
			'token',
			'/src/report.csv',
			'/src/summary.csv',
			'chat-1',
		);
	});

	it('uses the current directory if a rename source has no parent path', async () => {
		const { controller, moveEntry } = createController();

		await expect(
			controller.rename('report.csv', 'summary.csv'),
		).resolves.toEqual({
			status: 'renamed',
			destination: '/working/summary.csv',
			name: 'summary.csv',
		});
		expect(moveEntry).toHaveBeenCalledWith(
			'https://terminal.test',
			'token',
			'report.csv',
			'/working/summary.csv',
			'chat-1',
		);
	});
});
