import { describe, expect, it, vi } from 'vitest';
import { processComposerFile } from './fileProcessing';

const makeFile = (name: string, size = 10, type = 'text/plain') => ({ name, size, type }) as File;

const dependencies = (overrides: Record<string, any> = {}) => ({
	maximumFileSizeBytes: null,
	normalizeImageFile: vi.fn(async (file: File) => file),
	isImage: vi.fn((file: File) => file.type.startsWith('image/')),
	isHeicImage: vi.fn(() => false),
	convertHeicToJpeg: vi.fn(async (file: File) => file),
	compressImage: vi.fn(async (url: string) => `${url}?compressed`),
	settings: {},
	fileConfig: {},
	visionEnabled: true,
	temporary: false,
	readImageAsDataUrl: vi.fn(async (file: File) => `data:${file.name}`),
	fetchImageBlob: vi.fn(async () => ({ kind: 'blob' }) as unknown as Blob),
	createImageFile: vi.fn(
		(blob: Blob, name: string, type: string) => ({ blob, name, type }) as unknown as File
	),
	appendTemporaryImage: vi.fn(),
	uploadFile: vi.fn(async () => undefined),
	onFileTooLarge: vi.fn(),
	onVisionUnsupported: vi.fn(),
	...overrides
});

describe('processComposerFile', () => {
	it('checks the configured size limit before normalization or upload', async () => {
		const deps = dependencies({ maximumFileSizeBytes: 5 });

		await expect(processComposerFile(makeFile('large.txt', 6), deps)).resolves.toBe('skipped');

		expect(deps.onFileTooLarge).toHaveBeenCalledOnce();
		expect(deps.normalizeImageFile).not.toHaveBeenCalled();
		expect(deps.uploadFile).not.toHaveBeenCalled();
	});

	it('uploads normalized non-image files through the standard path', async () => {
		const original = makeFile('report.pdf', 12, 'application/pdf');
		const normalized = makeFile('report.pdf', 12, 'application/pdf');
		const deps = dependencies({ normalizeImageFile: vi.fn(async () => normalized) });

		await expect(processComposerFile(original, deps)).resolves.toBe('uploaded');

		expect(deps.normalizeImageFile).toHaveBeenCalledWith(original);
		expect(deps.uploadFile).toHaveBeenCalledWith(normalized);
	});

	it('stops image handling when no selected model accepts images', async () => {
		const deps = dependencies({ visionEnabled: false });

		await expect(processComposerFile(makeFile('photo.png', 12, 'image/png'), deps)).resolves.toBe(
			'skipped'
		);

		expect(deps.onVisionUnsupported).toHaveBeenCalledOnce();
		expect(deps.readImageAsDataUrl).not.toHaveBeenCalled();
		expect(deps.uploadFile).not.toHaveBeenCalled();
	});

	it('compresses and attaches temporary images without uploading them', async () => {
		const deps = dependencies({
			temporary: true,
			settings: { imageCompression: true, imageCompressionSize: { width: 1200, height: 900 } },
			fileConfig: { image_compression: { width: 800, height: 700 } }
		});

		await expect(processComposerFile(makeFile('photo.png', 12, 'image/png'), deps)).resolves.toBe(
			'temporary-image'
		);

		expect(deps.compressImage).toHaveBeenCalledWith('data:photo.png', 800, 700);
		expect(deps.appendTemporaryImage).toHaveBeenCalledWith('data:photo.png?compressed');
		expect(deps.uploadFile).not.toHaveBeenCalled();
	});

	it('converts and uploads a processed image with extraction disabled', async () => {
		const original = makeFile('photo.heic', 12, 'image/heic');
		const jpeg = makeFile('photo.jpg', 12, 'image/jpeg');
		const deps = dependencies({
			isImage: vi.fn(() => false),
			isHeicImage: vi.fn(() => true),
			convertHeicToJpeg: vi.fn(async () => jpeg)
		});

		await expect(processComposerFile(original, deps)).resolves.toBe('uploaded');

		expect(deps.convertHeicToJpeg).toHaveBeenCalledWith(original);
		expect(deps.createImageFile).toHaveBeenCalledWith({ kind: 'blob' }, 'photo.jpg', 'image/jpeg');
		expect(deps.uploadFile).toHaveBeenCalledWith(
			{ blob: { kind: 'blob' }, name: 'photo.jpg', type: 'image/jpeg' },
			false
		);
	});
});
