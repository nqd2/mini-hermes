import { afterEach, describe, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import { getTerminalServers, terminalRequest } from './index';

afterEach(() => {
	config.set(undefined);
	vi.unstubAllGlobals();
});

describe('existing terminal gateway consumer contract', () => {
	it('does not probe the gateway when tool servers are disabled', async () => {
		const fetch = vi.fn();
		vi.stubGlobal('fetch', fetch);
		expect(await getTerminalServers('token')).toEqual([]);
		await expect(
			terminalRequest({ selector: 'one', baseUrl: '/api/v1/terminals/one', key: 'token', system: true }, null, '/files/list')
		).rejects.toThrow('Tool servers are disabled');
		expect(fetch).not.toHaveBeenCalled();
	});

	it('sends the saved chat context and bearer token to the selected proxy path', async () => {
		config.set({ features: { enable_tool_servers: true } } as any);
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ entries: [] }) });
		vi.stubGlobal('fetch', fetch);
		const connection = {
			selector: 'one', baseUrl: '/api/v1/terminals/one', key: ' token ', system: true
		};
		expect(await terminalRequest(connection, 'chat-1', '/files/list', { method: 'GET' })).toEqual({ entries: [] });
		const [url, options] = fetch.mock.calls[0];
		expect(url).toBe('/api/v1/terminals/one/files/list');
		expect(options.headers.Authorization).toBe('Bearer token');
		expect(options.headers['X-Session-Id']).toBe('chat-1');
	});

	it('surfaces a proxy error string and uses a status fallback for unknown bodies', async () => {
		config.set({ features: { enable_tool_servers: true } } as any);
		const connection = {
			selector: 'one', baseUrl: '/api/v1/terminals/one', key: 'token', system: true
		};
		const fetch = vi.fn()
			.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ error: 'Access denied' }) })
			.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({ detail: { code: 'bad' } }) });
		vi.stubGlobal('fetch', fetch);
		await expect(terminalRequest(connection, null, '/files/list')).rejects.toThrow('Access denied');
		await expect(terminalRequest(connection, null, '/files/list')).rejects.toThrow('Terminal request failed (502)');
	});
});
