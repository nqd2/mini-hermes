import { downloadFileBlob, downloadFilePreview, readFile } from '$lib/apis/terminal';

export const OFFICE_EXTS = new Set(['docx', 'xlsx', 'xls', 'pptx']);

export const getFileExt = (path: string | null) => path?.split('.').pop()?.toLowerCase() ?? '';

export type FilePreviewData = {
	fileImageUrl: string | null;
	fileVideoUrl: string | null;
	fileAudioUrl: string | null;
	filePdfData: ArrayBuffer | null;
	fileSqliteData: ArrayBuffer | null;
	fileModelData: ArrayBuffer | null;
	fileDocxData: ArrayBuffer | null;
	fileOfficeHtml: string | null;
	fileOfficeSlides: string[] | null;
	excelWorkbook: any | null;
	excelSheetNames: string[];
	selectedExcelSheet: string;
	currentSlide: number;
	fileContent: string | null;
};

export type FilePreviewTranslator = (key: string, options?: { error?: string }) => string;

export type FilePreviewLoaderDependencies = {
	downloadFileBlob: typeof downloadFileBlob;
	downloadFilePreview: typeof downloadFilePreview;
	readFile: typeof readFile;
	createObjectURL: typeof URL.createObjectURL;
	loadXlsx: () => Promise<typeof import('xlsx')>;
	excelToTable: (worksheet: any) => Promise<{ html: string }>;
	sanitizeHtml: (html: string) => string | Promise<string>;
	pptxToImages: (buffer: ArrayBuffer) => Promise<{ images: string[] }>;
};

export type FilePreviewTerminal = { url: string; key: string };

const defaultDependencies: FilePreviewLoaderDependencies = {
	downloadFileBlob,
	downloadFilePreview,
	readFile,
	createObjectURL: (blob) => URL.createObjectURL(blob),
	loadXlsx: () => import('xlsx'),
	excelToTable: async (worksheet) => {
		const { excelToTable } = await import('$lib/utils/excelToTable');
		return excelToTable(worksheet);
	},
	sanitizeHtml: async (html) => {
		const DOMPurify = (await import('dompurify')).default;
		return DOMPurify.sanitize(html);
	},
	pptxToImages: async (buffer) => {
		const { pptxToImages } = await import('$lib/utils/pptxToHtml');
		return pptxToImages(buffer);
	}
};

const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'avif']);
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'ogv', 'avi', 'mkv']);
const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'oga', 'flac', 'm4a', 'aac', 'wma', 'opus']);
const SQLITE_EXTS = new Set(['db', 'sqlite', 'sqlite3', 'db3']);
const MODEL_EXTS = new Set(['glb', 'gltf', 'stl', 'obj', 'ply']);

export const createEmptyFilePreviewData = (): FilePreviewData => ({
	fileImageUrl: null,
	fileVideoUrl: null,
	fileAudioUrl: null,
	filePdfData: null,
	fileSqliteData: null,
	fileModelData: null,
	fileDocxData: null,
	fileOfficeHtml: null,
	fileOfficeSlides: null,
	excelWorkbook: null,
	excelSheetNames: [],
	selectedExcelSheet: '',
	currentSlide: 0,
	fileContent: null
});

export const loadFilePreviewData = async (
	path: string,
	terminal: FilePreviewTerminal,
	chatId: string | null,
	translate: FilePreviewTranslator,
	dependencies: FilePreviewLoaderDependencies = defaultDependencies
): Promise<FilePreviewData> => {
	const preview = createEmptyFilePreviewData();
	const ext = getFileExt(path);
	const sessionId = chatId ?? undefined;
	const download = () => dependencies.downloadFileBlob(terminal.url, terminal.key, path, sessionId);

	if (IMAGE_EXTS.has(ext) || VIDEO_EXTS.has(ext) || AUDIO_EXTS.has(ext)) {
		const result = await download();
		if (!result) return preview;
		const url = dependencies.createObjectURL(result.blob);
		if (IMAGE_EXTS.has(ext)) preview.fileImageUrl = url;
		else if (VIDEO_EXTS.has(ext)) preview.fileVideoUrl = url;
		else preview.fileAudioUrl = url;
		return preview;
	}

	if (ext === 'pdf') {
		const result = await download();
		if (result) preview.filePdfData = await result.blob.arrayBuffer();
		return preview;
	}

	if (SQLITE_EXTS.has(ext) || MODEL_EXTS.has(ext)) {
		const result = await download();
		if (!result) return preview;
		const data = await result.blob.arrayBuffer();
		if (MODEL_EXTS.has(ext)) preview.fileModelData = data;
		else preview.fileSqliteData = data;
		return preview;
	}

	if (OFFICE_EXTS.has(ext)) {
		try {
			if (ext === 'docx') {
				const rendered = await dependencies.downloadFilePreview(
					terminal.url,
					terminal.key,
					path,
					sessionId
				);
				if (rendered) {
					preview.filePdfData = await rendered.blob.arrayBuffer();
				} else {
					const result = await download();
					if (!result) throw new Error('Preview failed');
					preview.fileDocxData = await result.blob.arrayBuffer();
				}
			} else if (ext === 'xlsx') {
				const result = await download();
				if (result) {
					const arrayBuffer = await result.blob.arrayBuffer();
					const XLSX = await dependencies.loadXlsx();
					const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
					preview.excelWorkbook = workbook;
					preview.excelSheetNames = workbook.SheetNames;
					if (workbook.SheetNames.length > 0) {
						preview.selectedExcelSheet = workbook.SheetNames[0];
						const table = await dependencies.excelToTable(
							workbook.Sheets[preview.selectedExcelSheet]
						);
						preview.fileOfficeHtml = await dependencies.sanitizeHtml(table.html);
					}
				}
			} else if (ext === 'pptx') {
				const rendered = await dependencies.downloadFilePreview(
					terminal.url,
					terminal.key,
					path,
					sessionId
				);
				if (rendered) {
					preview.filePdfData = await rendered.blob.arrayBuffer();
				} else {
					const result = await download();
					if (!result) throw new Error('Preview failed');
					const fallback = await dependencies.pptxToImages(await result.blob.arrayBuffer());
					preview.fileOfficeSlides = fallback.images;
				}
			}
		} catch (error) {
			console.error('Failed to render Office file:', error);
			preview.fileContent = translate('Error previewing file: {{error}}', {
				error: error instanceof Error ? error.message : translate('Unknown error')
			});
		}
		return preview;
	}

	preview.fileContent = await dependencies.readFile(terminal.url, terminal.key, path, sessionId);
	return preview;
};
