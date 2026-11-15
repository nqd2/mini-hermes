import { describe, expect, it, vi } from 'vitest';
import { fetchAndUploadDriveFile } from './chatDriveUpload';

describe('Google Drive chat uploads', () => {
	it('passes audio language and the fetched file to the existing upload API', async () => {
		const uploader = vi.fn(async () => ({ id: 'uploaded', meta: { collection_name: 'files' } }));
		const fetcher = vi.fn(async () => new Response(new Blob(['audio'], { type: 'audio/wav' })));
		const result = await fetchAndUploadDriveFile(
			{ name: 'voice.wav', url: 'https://drive.example/voice', headers: { Authorization: 'Bearer token' } },
			'local-token', 'vi', uploader, fetcher
		);
		expect(fetcher).toHaveBeenCalledWith('https://drive.example/voice', {
			headers: { Authorization: 'Bearer token', Accept: '*/*' }, method: 'GET'
		});
		expect(uploader).toHaveBeenCalledWith('local-token', result.file, { language: 'vi' });
		expect(result.uploadedFile.id).toBe('uploaded');
	});

	it('rejects an empty fetched file before calling the upload API', async () => {
		const uploader = vi.fn();
		await expect(fetchAndUploadDriveFile(
			{ name: 'empty.txt', url: 'https://drive.example/empty', headers: { Authorization: 'Bearer token' } },
			'local-token', undefined, uploader, async () => new Response(new Blob([]))
		)).rejects.toThrow('Retrieved file is empty');
		expect(uploader).not.toHaveBeenCalled();
	});
});
