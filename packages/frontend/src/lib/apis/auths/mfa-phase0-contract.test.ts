import { afterEach, describe, expect, it, vi } from 'vitest';
import { mfaRequest } from './mfa';

const reply = (body: unknown, ok = true) => ({ ok, json: async () => body });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('existing MFA API consumer contract', () => {
	it.each([
		['status', 'GET'], ['challenge', 'POST'], ['enroll/start', 'POST'],
		['enroll/confirm', 'POST'], ['verify', 'POST'], ['replace', 'POST'],
		['recovery/codes', 'POST'], ['recover', 'POST']
	] as const)('uses %s via %s with same-origin credentials and no cache', async (path, method) => {
		const fetch = vi.fn().mockResolvedValue(reply({ ok: true }));
		vi.stubGlobal('fetch', fetch);
		expect(await mfaRequest(path)).toEqual({ ok: true });
		const [rawUrl, request] = fetch.mock.calls[0];
		expect(new URL(rawUrl, 'http://example.invalid').pathname).toBe(`/api/v1/auths/mfa/${path}`);
		expect(request).toMatchObject({ method, credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' } });
		expect(request.headers.Authorization).toBeUndefined();
		expect(request.body).toBeUndefined();
	});
	it('passes the challenge body and optional bearer token', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ recovery_codes: ['synthetic-code'] }));
		vi.stubGlobal('fetch', fetch);
		await mfaRequest('recovery/codes', { code: 'test-code', recovery: true }, 'test-token');
		const request = fetch.mock.calls[0][1];
		expect(request.headers.Authorization).toBe('Bearer test-token');
		expect(JSON.parse(request.body)).toEqual({ code: 'test-code', recovery: true });
	});
	it('raises an Error with backend detail or the fallback message', async () => {
		const fetch = vi.fn().mockResolvedValueOnce(reply({ detail: 'reauthentication_required' }, false)).mockResolvedValueOnce(reply({}, false));
		vi.stubGlobal('fetch', fetch);
		await expect(mfaRequest('challenge')).rejects.toThrow('reauthentication_required');
		await expect(mfaRequest('challenge')).rejects.toThrow('Authentication failed. Please try again.');
	});
});
