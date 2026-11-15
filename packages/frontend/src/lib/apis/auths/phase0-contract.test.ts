import { afterEach, describe, expect, it, vi } from 'vitest';
import { getSessionUser, userSignIn, userSignOut, userSignUp } from './index';

function reply(body: unknown, ok = true) {
	return { ok, json: async () => body };
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('existing authentication consumer contract', () => {
	it('sends email and password with cookies on sign in', async () => {
		const result = { token: 'test-token', token_type: 'Bearer', id: 'user-1', role: 'user' };
		const fetch = vi.fn().mockResolvedValue(reply(result));
		vi.stubGlobal('fetch', fetch);

		expect(await userSignIn('person@example.invalid', 'test-password')).toEqual(result);
		const [url, options] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/auths/signin');
		expect(options.method).toBe('POST');
		expect(options.credentials).toBe('include');
		expect(options.headers['Content-Type']).toBe('application/json');
		expect(JSON.parse(options.body)).toEqual({ email: 'person@example.invalid', password: 'test-password' });
	});

	it('supports the MFA challenge response without treating it as a session', async () => {
		const challenge = { next_step: 'verify', challenge_token: 'example', expires_in: 300 };
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(challenge)));
		expect(await userSignIn('person@example.invalid', 'test-password')).toEqual(challenge);
	});

	it('sends signup fields and cookies', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ next_step: 'pending' }));
		vi.stubGlobal('fetch', fetch);
		expect(await userSignUp('Person', 'person@example.invalid', 'test-password', '/user.png')).toEqual({
			next_step: 'pending'
		});
		const [url, options] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/auths/signup');
		expect(options.credentials).toBe('include');
		expect(JSON.parse(options.body)).toEqual({
			name: 'Person', email: 'person@example.invalid', password: 'test-password', profile_image_url: '/user.png'
		});
	});

	it('sends bearer credentials and cookies for session lookup', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ id: 'user-1', permissions: {} }));
		vi.stubGlobal('fetch', fetch);
		expect(await getSessionUser('example-token')).toEqual({ id: 'user-1', permissions: {} });
		const [url, options] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/auths/');
		expect(options.method).toBe('GET');
		expect(options.credentials).toBe('include');
		expect(options.headers.Authorization).toBe('Bearer example-token');
	});

	it('returns API detail on rejected authentication', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ detail: 'Invalid credentials' }, false)));
		vi.spyOn(console, 'error').mockImplementation(() => {});
		await expect(userSignIn('person@example.invalid', 'wrong')).rejects.toBe('Invalid credentials');
	});

	it('signs out with cookies and clears session storage on success', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ status: true }));
		const clear = vi.fn();
		vi.stubGlobal('fetch', fetch);
		vi.stubGlobal('sessionStorage', { clear });
		expect(await userSignOut()).toEqual({ status: true });
		const [url, options] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/auths/signout');
		expect(options.method).toBe('POST');
		expect(options.credentials).toBe('include');
		expect(clear).toHaveBeenCalledOnce();
	});
});
