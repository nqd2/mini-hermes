import { describe, expect, it, vi } from 'vitest';
import { loadFilePreviewData, type FilePreviewLoaderDependencies } from './filePreviewLoader';

const terminal = { url: 'https://terminal.example', key: 'terminal-secret' };

const makeDependencies = (
	overrides: Partial<FilePreviewLoaderDependencies> = {}
): FilePreviewLoaderDependencies => ({
	downloadFileBlob: vi.fn(async () => null),
	downloadFilePreview: vi.fn(async () => null),
	readFile: vi.fn(async () => 'file contents'),
	createObjectURL: vi.fn(() => 'blob:preview'),
	loadXlsx: vi.fn(async () => ({ read: vi.fn() }) as any),
	excelToTable: vi.fn(async () => ({ html: '<table>data</table>' })),
	sanitizeHtml: vi.fn(async (html) => html),
	pptxToImages: vi.fn(async () => ({ images: ['slide-1'] })),
	...overrides
});

const translate = (key: string, options?: { error?: string }) =>
	options?.error ? key.replace('{{error}}', options.error) : key;

describe('file preview loader', () => {
	it('reads text files through the terminal resource adapter', async () => {
		const dependencies = makeDependencies();

		const preview = await loadFilePreviewData(
			'/workspace/report.txt',
			terminal,
			'chat-1',
			translate,
			dependencies
		);

		expect(dependencies.readFile).toHaveBeenCalledWith(
			terminal.url,
			terminal.key,
			'/workspace/report.txt',
			'chat-1'
		);
		expect(preview.fileContent).toBe('file contents');
		expect(preview.filePdfData).toBeNull();
	});

	it('turns image blobs into preview URLs using the current chat scope', async () => {
		const blob = new Blob(['image']);
		const dependencies = makeDependencies({
			downloadFileBlob: vi.fn(async () => ({ blob, filename: 'photo.png' }))
		});

		const preview = await loadFilePreviewData(
			'/workspace/photo.png',
			terminal,
			'chat-2',
			translate,
			dependencies
		);

		expect(dependencies.downloadFileBlob).toHaveBeenCalledWith(
			terminal.url,
			terminal.key,
			'/workspace/photo.png',
			'chat-2'
		);
		expect(preview.fileImageUrl).toBe('blob:preview');
		expect(dependencies.createObjectURL).toHaveBeenCalledWith(blob);
	});

	it('loads the first worksheet and sanitizes generated spreadsheet HTML', async () => {
		const workbook = { SheetNames: ['Summary', 'Details'], Sheets: { Summary: { A1: { v: 4 } } } };
		const dependencies = makeDependencies({
			downloadFileBlob: vi.fn(async () => ({
				blob: new Blob(['workbook']),
				filename: 'report.xlsx'
			})),
			loadXlsx: vi.fn(async () => ({ read: vi.fn(() => workbook) }) as any),
			excelToTable: vi.fn(async () => ({ html: '<table><script>bad()</script></table>' })),
			sanitizeHtml: vi.fn(async () => '<table></table>')
		});

		const preview = await loadFilePreviewData(
			'/workspace/report.xlsx',
			terminal,
			'chat-3',
			translate,
			dependencies
		);

		expect(preview.excelSheetNames).toEqual(['Summary', 'Details']);
		expect(preview.selectedExcelSheet).toBe('Summary');
		expect(preview.excelWorkbook).toBe(workbook);
		expect(preview.fileOfficeHtml).toBe('<table></table>');
		expect(dependencies.sanitizeHtml).toHaveBeenCalledWith('<table><script>bad()</script></table>');
	});

	it('uses the server PDF preview for DOCX when it is available', async () => {
		const blob = new Blob(['pdf']);
		const dependencies = makeDependencies({
			downloadFilePreview: vi.fn(async () => ({ blob, filename: 'report.pdf' }))
		});

		const preview = await loadFilePreviewData(
			'/workspace/report.docx',
			terminal,
			'chat-4',
			translate,
			dependencies
		);

		expect(preview.filePdfData).toBeInstanceOf(ArrayBuffer);
		expect(preview.fileDocxData).toBeNull();
		expect(dependencies.downloadFileBlob).not.toHaveBeenCalled();
	});

	it('uses the local PPTX renderer when no server PDF preview is available', async () => {
		const dependencies = makeDependencies({
			downloadFileBlob: vi.fn(async () => ({ blob: new Blob(['pptx']), filename: 'deck.pptx' })),
			pptxToImages: vi.fn(async () => ({ images: ['data:image/png;base64,slide'] }))
		});

		const preview = await loadFilePreviewData(
			'/workspace/deck.pptx',
			terminal,
			'chat-5',
			translate,
			dependencies
		);

		expect(preview.fileOfficeSlides).toEqual(['data:image/png;base64,slide']);
		expect(preview.filePdfData).toBeNull();
		expect(dependencies.pptxToImages).toHaveBeenCalledWith(expect.any(ArrayBuffer));
	});

	it('returns a visible error when an Office preview cannot be loaded', async () => {
		const logError = vi.spyOn(console, 'error').mockImplementation(() => {});
		const dependencies = makeDependencies({
			downloadFilePreview: vi.fn(async () => {
				throw new Error('preview service unavailable');
			})
		});

		const preview = await loadFilePreviewData(
			'/workspace/slides.pptx',
			terminal,
			null,
			translate,
			dependencies
		);

		expect(preview.fileContent).toBe('Error previewing file: preview service unavailable');
		expect(preview.fileOfficeSlides).toBeNull();
		logError.mockRestore();
	});
});
