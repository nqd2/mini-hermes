import { afterEach, describe, expect, it, vi } from 'vitest';
import { OPENAI_API_BASE_URL, WEAI_BASE_URL } from '$lib/constants';
import {
	chatCompletion,
	getErrorMessage,
	generateOpenAIChatCompletion,
	getOpenAIModels,
	getOpenAIModelsDirect,
	verifyOpenAIConnection
} from './index';

afterEach(() => vi.unstubAllGlobals());

describe('existing OpenAI adapter transport contract', () => {
	it('displays structured context errors received through chat message events', () => {
		const content = { code: 'model_context_budget_exceeded', message: 'Input budget exceeded.' };
		expect(getErrorMessage({ content })).toBe('Input budget exceeded.');
		expect(getErrorMessage({ error: { content } })).toBe('Input budget exceeded.');
	});
	it('uses the WeAI proxy for indexed model discovery and keeps the provider error text', async () => {
		const fetch = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ data: [{ id: 'model-a' }] }) })
			.mockResolvedValueOnce({ ok: false, json: async () => ({ error: { message: 'offline' } }) });
		vi.stubGlobal('fetch', fetch);

		expect(await getOpenAIModels('session-token', 0)).toEqual({ data: [{ id: 'model-a' }] });
		const [url, options] = fetch.mock.calls[0];
		expect(url).toBe(`${OPENAI_API_BASE_URL}/models/0`);
		expect(options.headers.authorization).toBe('Bearer session-token');
		await expect(getOpenAIModels('session-token', 0)).rejects.toBe('OpenAI: offline');
	});

	it('sends direct model discovery to the provider with its key, outside the WeAI proxy', async () => {
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: [] }) });
		vi.stubGlobal('fetch', fetch);

		expect(await getOpenAIModelsDirect('https://provider.example.invalid/v1', 'provider-key')).toEqual({ data: [] });
		const [url, options] = fetch.mock.calls[0];
		expect(url).toBe('https://provider.example.invalid/v1/models');
		expect(options.headers.authorization).toBe('Bearer provider-key');
	});

	it('keeps direct verification separate from administrator proxy verification', async () => {
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: [] }) });
		vi.stubGlobal('fetch', fetch);
		const connection = { url: 'https://provider.example.invalid/v1', key: 'provider-key', config: { provider: 'custom' } };

		await verifyOpenAIConnection('session-token', connection, true);
		await verifyOpenAIConnection('session-token', connection, false);
		expect(fetch.mock.calls[0][0]).toBe('https://provider.example.invalid/v1/models');
		expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer provider-key');
		expect(fetch.mock.calls[1][0]).toBe(`${OPENAI_API_BASE_URL}/verify`);
		expect(fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer session-token');
		expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(connection);
	});

	it('distinguishes raw HTTP completion response from JSON chat acceptance', async () => {
		const raw = { ok: false, status: 503 };
		const fetch = vi.fn()
			.mockResolvedValueOnce(raw)
			.mockResolvedValueOnce({ ok: true, json: async () => ({ status: true, task_ids: ['task-1'] }) });
		vi.stubGlobal('fetch', fetch);

		const [response] = await chatCompletion('session-token', { model: 'model-a' });
		expect(response).toBe(raw);
		expect(fetch.mock.calls[0][0]).toBe(`${WEAI_BASE_URL}/api/chat/completions`);
		expect(fetch.mock.calls[0][1].credentials).toBeUndefined();
		expect(await generateOpenAIChatCompletion('session-token', { model: 'model-a' })).toEqual({
			status: true, task_ids: ['task-1']
		});
		expect(fetch.mock.calls[1][1].credentials).toBe('include');
	});
});
