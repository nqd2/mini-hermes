import { afterEach, expect, it, vi } from 'vitest';
import { getErrorMessage } from '../openai';
import { getMyPlan, saveRouting, saveUserPlan, type RoutingConfig } from './tiers';

afterEach(() => {
	vi.unstubAllGlobals();
});

it('reports the current plan allowance and reset for the authenticated user', async () => {
	const allowance = { tier: 'plus', monthly_request_limit: 50, used: 49, remaining: 1, reset_at: '2026-12-01T00:00:00Z' };
	const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => allowance });
	vi.stubGlobal('fetch', fetch);
	await expect(getMyPlan('token')).resolves.toEqual(allowance);
	expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/agent-runtime\/me\/plan$/), expect.objectContaining({
		method: 'GET',
		headers: expect.objectContaining({ Authorization: 'Bearer token' })
	}));
});

it('passes an integer limit in the independent admin plan action', async () => {
	const plan = { tier: 'plus', monthly_request_limit: 8 } as const;
	const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => plan });
	vi.stubGlobal('fetch', fetch);
	await saveUserPlan('admin-token', 'user/id', plan);
	expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/admin\/users\/user%2Fid\/plan$/), expect.objectContaining({
		method: 'PUT',
		body: JSON.stringify(plan)
	}));
});

it('shows quota messages and surfaces concurrent edits to pool settings', async () => {
	const fetch = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: { code: 'model_pool_changed' } }) });
	vi.stubGlobal('fetch', fetch);
	const config: RoutingConfig = {
		version: 1, groups: {}, free_user_daily_limit: 2, free_account_daily_limit: 50, free_account_minute_limit: 20
	};
	await expect(saveRouting('admin-token', config)).rejects.toThrow('model_pool_changed');
	const quota = { code: 'quota_exhausted', message: 'You have used all requests in your WeAI allowance.' };
	expect(getErrorMessage({ error: { content: quota } })).toBe(quota.message);
});
