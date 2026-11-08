import { describe, expect, it, vi } from 'vitest';
import { createFileUploadController } from './fileUploadController';

describe('createFileUploadController', () => {
	const createController = (overrides: Record<string, unknown> = {}) => {
		const uploadFile = vi.fn(async () => ({ path: '/workspace/file.txt' }));
		const createDirectory = vi.fn(async () => ({ path: '/workspace/folder' }));
		return {
			controller: createFileUploadController({
				uploadFile,
				createDirectory,
				...overrides,
			}),
			uploadFile,
			createDirectory,
		};
	};

	it('uploads files and creates directories under the selected destination in order', async () => {
		const { controller, uploadFile, createDirectory } = createController();
		const terminal = { url: 'https://terminal.test', key: 'token' };
		const file = { name: 'report.csv' } as File;

		await controller.upload(
			[
				{ path: 'reports', file: undefined },
				{ path: 'reports/report.csv', file },
			],
			terminal,
			'/workspace/',
			'chat-1',
		);

		expect(createDirectory).toHaveBeenCalledWith(
			'https://terminal.test',
			'token',
			'/workspace/reports',
			'chat-1',
		);
		expect(uploadFile).toHaveBeenCalledWith(
			'https://terminal.test',
			'token',
			'/workspace/reports',
			file,
			'chat-1',
		);
		expect(createDirectory.mock.invocationCallOrder[0]).toBeLessThan(
			uploadFile.mock.invocationCallOrder[0],
		);
	});

	it.each([
		'',
		'/absolute.txt',
		'folder//file.txt',
		'./file.txt',
		'../outside.txt',
		'a\\b.txt',
		'a\0b.txt',
	])(
		'rejects unsafe relative paths such as %s before calling the adapter',
		async (path) => {
			const { controller, uploadFile, createDirectory } = createController();

			await expect(
				controller.upload(
					[{ path, file: { name: path } as File }],
					{
						url: 'https://terminal.test',
						key: 'token',
					},
					'/workspace',
				),
			).rejects.toThrow(`Invalid upload path: ${path}`);
			expect(uploadFile).not.toHaveBeenCalled();
			expect(createDirectory).not.toHaveBeenCalled();
		},
	);

	it('stops the batch and identifies the failed path when an adapter returns no result', async () => {
		const uploadFile = vi
			.fn(async () => null as unknown)
			.mockResolvedValueOnce({ path: '/workspace/first.txt' })
			.mockResolvedValueOnce(null);
		const { controller } = createController({ uploadFile });

		await expect(
			controller.upload(
				[
					{ path: 'first.txt', file: { name: 'first.txt' } as File },
					{ path: 'second.txt', file: { name: 'second.txt' } as File },
					{ path: 'third.txt', file: { name: 'third.txt' } as File },
				],
				{ url: 'https://terminal.test', key: 'token' },
				'/',
				'chat-2',
			),
		).rejects.toThrow('second.txt');
		expect(uploadFile).toHaveBeenCalledTimes(2);
	});
});
