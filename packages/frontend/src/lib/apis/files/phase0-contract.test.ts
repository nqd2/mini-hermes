import { afterEach, describe, expect, it, vi } from 'vitest';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import { getFileContentById, getFileProcessStatus, uploadFile } from './index';

afterEach(() => vi.unstubAllGlobals());

describe('existing file upload consumer contract', () => {
	it('submits multipart bytes and metadata, then exposes the uploaded file ID', async () => {
		const uploaded = vi.fn();
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'file-1', filename: 'book.xlsx' }) });
		vi.stubGlobal('fetch', fetch);
		const file = new File(['workbook bytes'], 'book.xlsx', {
			type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
		});
		const result = await uploadFile('token', file, { mini_hermes_chat_upload: true }, false, false, uploaded);
		const [url, options] = fetch.mock.calls[0];
		expect(url).toBe(`${WEBUI_API_BASE_URL}/files/?process=false`);
		expect(options.method).toBe('POST');
		expect(options.headers.authorization).toBe('Bearer token');
		expect(options.body.get('file')).toBe(file);
		expect(options.body.get('metadata')).toBe('{"mini_hermes_chat_upload":true}');
		expect(uploaded).toHaveBeenCalledWith(result);
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('opens the processing stream with bearer auth and stream=true', async () => {
		const response = { ok: true };
		const fetch = vi.fn().mockResolvedValue(response);
		vi.stubGlobal('fetch', fetch);
		expect(await getFileProcessStatus('token', 'file-1')).toBe(response);
		expect(fetch).toHaveBeenCalledWith(`${WEBUI_API_BASE_URL}/files/file-1/process/status?stream=true`, {
			method: 'GET',
			headers: { Accept: 'application/json', authorization: 'Bearer token' }
		});
	});

	it('propagates upload error detail but returns a non-OK status response unchanged', async () => {
		const denied = { ok: false, status: 404, json: async () => ({ detail: 'Not found' }) };
		const fetch = vi.fn().mockResolvedValueOnce(denied).mockResolvedValueOnce(denied);
		vi.stubGlobal('fetch', fetch);
		const file = new File(['x'], 'book.xlsx');
		await expect(uploadFile('token', file, null, false, false)).rejects.toBe('Not found');
		expect(await getFileProcessStatus('token', 'file-1')).toBe(denied);
	});

	it('downloads original bytes with the session cookie instead of a bearer argument', async () => {
		const bytes = new Uint8Array([1, 2, 3]).buffer;
		const fetch = vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => bytes });
		vi.stubGlobal('fetch', fetch);
		expect(await getFileContentById('file-1')).toBe(bytes);
		expect(fetch).toHaveBeenCalledWith(`${WEBUI_API_BASE_URL}/files/file-1/content`, {
			method: 'GET', headers: { Accept: 'application/json' }, credentials: 'include'
		});
	});
});
