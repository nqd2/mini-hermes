import { afterEach, describe, expect, it, vi } from 'vitest';
import { createNewChat, getChatById, getChatList } from './index';

function reply(body: unknown, ok = true) {
	return { ok, json: async () => body };
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('existing chat API consumer contract', () => {
	it('sends the supplied chat, folder and optional variables when creating a chat', async () => {
		const fetch = vi.fn().mockResolvedValue(reply({ id: 'chat-1', chat: { title: 'Research' } }));
		vi.stubGlobal('fetch', fetch);
		const result = await createNewChat('test-token', { title: 'Research' }, 'folder-1', { topic: 'labs' });
		expect(result.id).toBe('chat-1');
		const [url, request] = fetch.mock.calls[0];
		expect(new URL(url, 'http://example.invalid').pathname).toBe('/api/v1/chats/new');
		expect(request.method).toBe('POST');
		expect(request.headers.authorization).toBe('Bearer test-token');
		expect(JSON.parse(request.body)).toEqual({
			chat: { title: 'Research' }, folder_id: 'folder-1', variables: { topic: 'labs' }
		});
	});

	it('requests paginated chats with the requested filters', async () => {
		const fetch = vi.fn().mockResolvedValue(reply([{ id: 'chat-1', updated_at: 1_700_000_000 }]));
		vi.stubGlobal('fetch', fetch);
		const result = await getChatList('test-token', 2, true, true);
		expect(result[0]).toMatchObject({ id: 'chat-1', updated_at: 1_700_000_000 });
		const [url, request] = fetch.mock.calls[0];
		const parsed = new URL(url, 'http://example.invalid');
		expect(parsed.pathname).toBe('/api/v1/chats/');
		expect(Object.fromEntries(parsed.searchParams)).toEqual({
			page: '2', include_folders: 'true', include_pinned: 'true'
		});
		expect(request.headers.authorization).toBe('Bearer test-token');
	});

	it('retrieves a chat by id and propagates a structured API error', async () => {
		const fetch = vi.fn()
			.mockResolvedValueOnce(reply({ id: 'chat-1', chat: { messages: [] } }))
			.mockResolvedValueOnce(reply({ detail: 'Chat not found' }, false));
		vi.stubGlobal('fetch', fetch);
		vi.spyOn(console, 'error').mockImplementation(() => {});
		expect(await getChatById('test-token', 'chat-1')).toMatchObject({ id: 'chat-1' });
		expect(new URL(fetch.mock.calls[0][0], 'http://example.invalid').pathname).toBe('/api/v1/chats/chat-1');
		await expect(getChatById('test-token', 'missing')).rejects.toBe('Chat not found');
	});
});
