export type ComposerFileProcessorDependencies = {
	maximumFileSizeBytes?: number | null;
	normalizeImageFile: (file: File) => Promise<File> | File;
	isImage: (file: File) => boolean;
	isHeicImage: (file: File) => boolean;
	convertHeicToJpeg: (file: File) => Promise<File>;
	compressImage: (url: string, width: number | null, height: number | null) => Promise<string>;
	settings?: Record<string, any>;
	fileConfig?: Record<string, any>;
	visionEnabled: boolean;
	temporary: boolean;
	readImageAsDataUrl: (file: File) => Promise<string>;
	fetchImageBlob: (url: string) => Promise<Blob>;
	createImageFile: (blob: Blob, name: string, type: string) => File;
	appendTemporaryImage: (url: string) => void;
	uploadFile: (file: File, process?: boolean) => Promise<unknown>;
	onFileTooLarge: () => void;
	onVisionUnsupported: () => void;
};

type ComposerFileProcessingResult = 'skipped' | 'uploaded' | 'temporary-image';

const compressImageIfNeeded = async (
	imageUrl: string,
	settings: Record<string, any> | undefined,
	fileConfig: Record<string, any> | undefined,
	compressImage: ComposerFileProcessorDependencies['compressImage']
) => {
	const settingsCompression = settings?.imageCompression ?? false;
	const configWidth = fileConfig?.image_compression?.width ?? null;
	const configHeight = fileConfig?.image_compression?.height ?? null;
	if (!settingsCompression && !configWidth && !configHeight) return imageUrl;

	let width = settingsCompression ? (settings?.imageCompressionSize?.width ?? null) : null;
	let height = settingsCompression ? (settings?.imageCompressionSize?.height ?? null) : null;
	if (configWidth && (width === null || width > configWidth)) width = configWidth;
	if (configHeight && (height === null || height > configHeight)) height = configHeight;
	return width || height ? compressImage(imageUrl, width, height) : imageUrl;
};

/** Normalize, validate and route one composer file through the correct upload path. */
export const processComposerFile = async (
	inputFile: File,
	dependencies: ComposerFileProcessorDependencies
): Promise<ComposerFileProcessingResult> => {
	const { maximumFileSizeBytes } = dependencies;
	if (
		maximumFileSizeBytes !== null &&
		maximumFileSizeBytes !== undefined &&
		inputFile.size > maximumFileSizeBytes
	) {
		dependencies.onFileTooLarge();
		return 'skipped';
	}

	const file = await dependencies.normalizeImageFile(inputFile);
	const isImage = dependencies.isImage(file) || dependencies.isHeicImage(file);
	if (!isImage) {
		await dependencies.uploadFile(file);
		return 'uploaded';
	}

	if (!dependencies.visionEnabled) {
		dependencies.onVisionUnsupported();
		return 'skipped';
	}

	const imageFile = dependencies.isHeicImage(file)
		? await dependencies.convertHeicToJpeg(file)
		: file;
	let imageUrl = await dependencies.readImageAsDataUrl(imageFile);
	imageUrl = await compressImageIfNeeded(
		imageUrl,
		dependencies.settings,
		dependencies.fileConfig,
		dependencies.compressImage
	);

	if (dependencies.temporary) {
		dependencies.appendTemporaryImage(imageUrl);
		return 'temporary-image';
	}

	const blob = await dependencies.fetchImageBlob(imageUrl);
	const compressedFile = dependencies.createImageFile(blob, imageFile.name, imageFile.type);
	await dependencies.uploadFile(compressedFile, false);
	return 'uploaded';
};
