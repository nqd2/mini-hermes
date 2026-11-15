import { afterEach, describe, expect, it, vi } from 'vitest';
import { createNewModel, exportModels, getModelItems, importModels, updateModelById } from './index';
const reply = (body: unknown, ok = true) => ({ ok, json: async () => body });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('existing model management consumer contract', () => {
	it('sends list filters, bearer token and signal', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ items: [{ id: 'model-1', write_access: false }], total: 1 }));
		vi.stubGlobal('fetch', fetch);
		const signal = new AbortController().signal;
		const result = await getModelItems('test-token', 'hello', 'shared', 'tag A', 'name', 'asc', 2, signal);
		const [rawUrl, request] = fetch.mock.calls[0];
		const url = new URL(rawUrl, 'http://example.invalid');
		expect(url.pathname).toBe('/api/v1/models/list');
		expect(Object.fromEntries(url.searchParams)).toEqual({ query: 'hello', view_option: 'shared', tag: 'tag A', order_by: 'name', direction: 'asc', page: '2' });
		expect(request).toMatchObject({ method: 'GET', signal, headers: { authorization: 'Bearer test-token' } });
		expect(result).toEqual({ items: [{ id: 'model-1', write_access: false }], total: 1 });
	});
	it('sends create and update fields with distinct access-grant behavior', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ id: 'model-1' }));
		vi.stubGlobal('fetch', fetch);
		const model = { id: 'model-1', base_model_id: 'base', name: 'My model', meta: {}, params: { temperature: 0.5 }, access_grants: [{ permission: 'read' }], is_active: true, commit_message: 'save', ignored: 'drop' };
		await createNewModel('test-token', model);
		await updateModelById('test-token', 'model-1', model);
		const [createUrl, createRequest] = fetch.mock.calls[0];
		const [updateUrl, updateRequest] = fetch.mock.calls[1];
		expect(new URL(createUrl, 'http://example.invalid').pathname).toBe('/api/v1/models/create');
		expect(new URL(updateUrl, 'http://example.invalid').pathname).toBe('/api/v1/models/model/update');
		expect(createRequest.method).toBe('POST');
		expect(updateRequest.headers.authorization).toBe('Bearer test-token');
		const { ignored, ...expectedCreate } = model;
		expect(JSON.parse(createRequest.body)).toEqual(expectedCreate);
		const { access_grants, ...expectedUpdate } = expectedCreate;
		expect(JSON.parse(updateRequest.body)).toEqual(expectedUpdate);
	});
	it('exports repeated ids and skips an empty selection', async () => {
		const fetch = vi.fn().mockResolvedValue(reply([{ id: 'a' }, { id: 'b' }]));
		vi.stubGlobal('fetch', fetch);
		expect(await exportModels('test-token', [])).toEqual([]);
		expect(fetch).not.toHaveBeenCalled();
		await exportModels('test-token', ['a', 'b']);
		const [rawUrl, request] = fetch.mock.calls[0];
		const url = new URL(rawUrl, 'http://example.invalid');
		expect(url.pathname).toBe('/api/v1/models/export');
		expect(url.searchParams.getAll('ids')).toEqual(['a', 'b']);
		expect(request.headers.authorization).toBe('Bearer test-token');
	});
	it('posts the models array and propagates a structured import error', async () => {
		const failure = { detail: 'Access prohibited' };
		const fetch = vi.fn().mockResolvedValueOnce(reply(true)).mockResolvedValueOnce(reply(failure, false));
		vi.stubGlobal('fetch', fetch);
		vi.spyOn(console, 'error').mockImplementation(() => {});
		expect(await importModels('test-token', [{ id: 'a' }])).toBe(true);
		const [url, request] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/models/import');
		expect(request.method).toBe('POST');
		expect(JSON.parse(request.body)).toEqual({ models: [{ id: 'a' }] });
		await expect(importModels('test-token', [])).rejects.toEqual(failure);
	});
});
