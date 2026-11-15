import { afterEach, describe, expect, it, vi } from 'vitest';
import { WEBUI_BASE_URL } from '$lib/constants';
import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { getTaskIdsByChatId, resumeTemporaryChat } from '$lib/apis';

afterEach(() => vi.unstubAllGlobals());

describe('existing chat start and reconnect consumer boundary', () => {
	it('posts an authenticated chat request and reads task IDs from JSON', async () => {
		const response = { status: true, task_ids: ['task-1'], chat_id: 'chat-1' };
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
		vi.stubGlobal('fetch', fetch);
		const payload = { model: 'model-1', messages: [{ role: 'user', content: 'Read workbook' }] };
		expect(await generateOpenAIChatCompletion('token', payload)).toEqual(response);
		expect(fetch).toHaveBeenCalledWith(`${WEBUI_BASE_URL}/api/chat/completions`, {
			method: 'POST',
			headers: { Authorization: 'Bearer token', 'Content-Type': 'application/json' },
			credentials: 'include',
			body: JSON.stringify(payload)
		});
	});

	it('looks up active task IDs by saved chat ID after reconnect', async () => {
		const response = { task_ids: ['task-1'] };
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
		vi.stubGlobal('fetch', fetch);
		expect(await getTaskIdsByChatId('token', 'chat/a')).toEqual(response);
		expect(fetch).toHaveBeenCalledWith(`${WEBUI_BASE_URL}/api/tasks/chat/chat%2Fa`, {
			signal: undefined,
			method: 'GET',
			headers: {
				Accept: 'application/json', 'Content-Type': 'application/json', authorization: 'Bearer token'
			}
		});
	});

	it('sends a new session ID when resuming a temporary chat', async () => {
		const response = { task_ids: ['task-1'], streams: [{ message_id: 'message-1', output: [] }] };
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
		vi.stubGlobal('fetch', fetch);
		expect(await resumeTemporaryChat('token', 'chat/a', 'socket-new')).toEqual(response);
		expect(fetch).toHaveBeenCalledWith(`${WEBUI_BASE_URL}/api/tasks/chat/chat%2Fa/resume`, {
			method: 'POST',
			headers: { Authorization: 'Bearer token', 'Content-Type': 'application/json' },
			body: JSON.stringify({ session_id: 'socket-new' })
		});
	});
});
