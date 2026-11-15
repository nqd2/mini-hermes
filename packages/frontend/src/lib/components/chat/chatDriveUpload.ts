type DriveFileData = {
	name: string;
	url: string;
	headers: { Authorization: string };
};

type UploadFile = (
	token: string,
	file: File,
	metadata: { language: string } | null
) => Promise<any>;

export async function fetchAndUploadDriveFile(
	fileData: DriveFileData,
	token: string,
	language: string | undefined,
	uploader: UploadFile,
	fetcher: typeof fetch = fetch
) {
	console.log('Processing web file with URL:', fileData.url);
	const fetchOptions = {
		headers: { Authorization: fileData.headers.Authorization, Accept: '*/*' },
		method: 'GET'
	};
	console.log('Fetching file content from Google Drive...');
	const response = await fetcher(fileData.url, fetchOptions);
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to fetch file (${response.status}): ${errorText}`);
	}

	const contentType = response.headers.get('content-type') || 'application/octet-stream';
	console.log('Response received with content-type:', contentType);
	console.log('Converting response to blob...');
	const blob = await response.blob();
	if (blob.size === 0) throw new Error('Retrieved file is empty');
	console.log('Blob created:', { size: blob.size, type: blob.type || contentType });

	const file = new File([blob], fileData.name, { type: blob.type || contentType });
	console.log('File object created:', { name: file.name, size: file.size, type: file.type });
	if (file.size === 0) throw new Error('Created file is empty');
	const metadata =
		(file.type.startsWith('audio/') || file.type.startsWith('video/')) && language
			? { language }
			: null;

	console.log('Uploading file to server...');
	const uploadedFile = await uploader(token, file, metadata);
	if (!uploadedFile) throw new Error('Server returned null response for file upload');
	console.log('File uploaded successfully:', uploadedFile);
	return { file, uploadedFile };
}
