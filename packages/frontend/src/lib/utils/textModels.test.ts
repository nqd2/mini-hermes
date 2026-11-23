import { describe, expect, it } from 'vitest';
import { isTextGenerationModel } from './textModels';

describe('isTextGenerationModel', () => {
	it('accepts text and multimodal chat models that generate text', () => {
		const validModels = [
			'gpt-4o',
			'gpt-4.1-mini',
			'gpt-5.4',
			'o3-mini',
			'claude-3-7-sonnet-latest',
			'google/gemini-2.5-pro',
			'meta-llama/llama-3.2-11b-vision-instruct:free',
			'qwen/qwen-2.5-vl-72b-instruct:free',
			'deepseek/deepseek-r1:free',
			'agent-runtime-free-small',
			'agent-runtime-plus-large',
			{
				id: 'google/gemma-3-27b-it:free',
				architecture: {
					modality: 'text+image->text',
					input_modalities: ['text', 'image'],
					output_modalities: ['text']
				}
			},
			{
				id: 'qwen2.5-vl-7b',
				type: 'vlm'
			}
		];

		for (const model of validModels) {
			expect(isTextGenerationModel(model), JSON.stringify(model)).toBe(true);
		}
	});

	it('rejects embedding, rerank, audio, image, video, moderation, and legacy non-chat models', () => {
		const nonTextModels = [
			'text-embedding-3-small',
			'text-embedding-ada-002',
			'nvidia/llama-nemotron-embed-vl-1b-v2:free',
			'baai/bge-large-en-v1.5',
			'bge-reranker-v2-m3',
			'omni-moderation-latest',
			'text-moderation-latest',
			'whisper-1',
			'whisper-large-v3',
			'tts-1',
			'tts-1-hd',
			'gpt-4o-mini-tts',
			'gpt-4o-transcribe',
			'gpt-4o-transcribe-diarize',
			'gpt-4o-realtime-preview',
			'gpt-4o-audio-preview',
			'google/lyria-3-pro:free',
			'dall-e-3',
			'gpt-image-1',
			'chatgpt-image-latest',
			'black-forest-labs/flux-1-schnell:free',
			'google/imagen-3',
			'stable-diffusion-xl',
			'gemini-2.5-flash-image-preview',
			'gemini-2.5-flash-image',
			'grok-2-image-1212',
			'sora-2',
			'babbage-002',
			'davinci-002',
			{
				id: 'custom/embed-v1:free',
				architecture: {
					output_modalities: ['embeddings']
				}
			},
			{
				id: 'custom/image-gen:free',
				architecture: {
					modality: 'text->image'
				}
			},
			{
				id: 'local-embedder',
				type: 'embeddings'
			}
		];

		for (const model of nonTextModels) {
			expect(isTextGenerationModel(model), JSON.stringify(model)).toBe(false);
		}
	});

	it('respects manual model ID overrides unless metadata explicitly marks non-text output', () => {
		expect(
			isTextGenerationModel(
				{ id: 'custom-embed-named-chat', manual_model_id: true },
				{ allowManualOverride: false }
			)
		).toBe(true);
		expect(
			isTextGenerationModel('custom-tts-named-chat', { allowManualOverride: true })
		).toBe(true);
		expect(
			isTextGenerationModel(
				{
					id: 'custom-non-text',
					manual_model_id: true,
					architecture: { output_modalities: ['embeddings'] }
				},
				{ allowManualOverride: true }
			)
		).toBe(false);
	});
});
