import { describe, expect, it } from 'vitest';
import { createOpenAITextStream } from './index';

function responseStream(parts: string[]): ReadableStream<Uint8Array> {
	const encoder = new TextEncoder();
	return new ReadableStream({
		start(controller) {
			for (const part of parts) controller.enqueue(encoder.encode(part));
			controller.close();
		}
	});
}

async function readUpdates(parts: string[]) {
	const updates = await createOpenAITextStream(responseStream(parts), false);
	const result = [];
	for await (const update of updates) result.push(update);
	return result;
}

describe('existing chat SSE consumer contract', () => {
	it('handles frames split across transport chunks and stops at DONE', async () => {
		const result = await readUpdates([
			'data: {"choices":[{"delta":{"content":"hel',
			'lo"}}]}\n\ndata: [DO',
			'NE]\n\ndata: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
		]);
		expect(result).toEqual([{ done: false, value: 'hello' }, { done: true, value: '' }]);
	});

	it('passes through source, model selection and usage events', async () => {
		const result = await readUpdates([
			'data: {"sources":[{"id":"source-1"}]}\n\n',
			'data: {"selected_model_id":"model-1"}\n\n',
			'data: {"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
			'data: [DONE]\n\n'
		]);
		expect(result).toEqual([
			{ done: false, value: '', sources: [{ id: 'source-1' }] },
			{ done: false, value: '', selectedModelId: 'model-1' },
			{ done: false, value: '', usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } },
			{ done: true, value: '' }
		]);
	});

	it('returns provider errors without reading following events', async () => {
		const result = await readUpdates([
			'data: {"error":{"message":"unavailable"}}\n\n',
			'data: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
		]);
		expect(result).toEqual([{ done: true, value: '', error: { message: 'unavailable' } }]);
	});

	it('treats end of stream without DONE as done', async () => {
		const result = await readUpdates(['data: {"choices":[{"delta":{"content":"partial"}}]}\n\n']);
		expect(result).toEqual([{ done: false, value: 'partial' }, { done: true, value: '' }]);
	});
});
