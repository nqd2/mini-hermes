import { describe, expect, it } from 'vitest';
import {
	formatModelNameWithProvider,
	getProviderTag,
	resolveProviderTagFromUrl
} from './providerTag';

describe('providerTag utility', () => {
	it('returns [WeAI] for Free and Plus logical tier models', () => {
		expect(getProviderTag({ id: 'agent-runtime-free-default', owned_by: 'agent-runtime' })).toBe(
			'[WeAI]'
		);
		expect(getProviderTag({ id: 'agent-runtime-free-large' })).toBe('[WeAI]');
		expect(getProviderTag({ id: 'agent-runtime-plus-small' })).toBe('[WeAI]');
		expect(getProviderTag(null, 'agent-runtime-plus-large')).toBe('[WeAI]');
	});

	it('prioritizes provider connection URL when available', () => {
		expect(
			getProviderTag({
				id: 'baidu/ernie-4.5-8k-preview',
				url: 'https://openrouter.ai/api/v1'
			})
		).toBe('[OpnR]');

		expect(
			getProviderTag({
				id: 'gpt-5.4-mini',
				url: 'https://api.openai.com/v1'
			})
		).toBe('[OpnAI]');

		expect(
			getProviderTag({
				id: 'claude-3-7-sonnet',
				url: 'https://api.anthropic.com/v1'
			})
		).toBe('[Anth]');

		expect(
			getProviderTag({
				id: 'gemini-2.5-pro',
				url: 'https://aiplatform.googleapis.com/v1'
			})
		).toBe('[GVrtx]');

		expect(
			getProviderTag({
				id: 'gpt-4o',
				url: 'https://api.1min.ai/openai/v1'
			})
		).toBe('[1Min]');
	});

	it('falls back to model slug or prefix when URL is absent', () => {
		expect(getProviderTag({ id: 'openai/gpt-4o' })).toBe('[OpnAI]');
		expect(getProviderTag({ id: 'gpt-4o-mini' })).toBe('[OpnAI]');
		expect(getProviderTag({ id: 'anthropic/claude-3.5-sonnet' })).toBe('[Anth]');
		expect(getProviderTag({ id: 'google/gemini-2.5-flash' })).toBe('[GVrtx]');
		expect(getProviderTag({ id: 'deepseek/deepseek-r1' })).toBe('[Dpsk]');
		expect(getProviderTag({ id: 'mistralai/mistral-large' })).toBe('[Mist]');
		expect(getProviderTag({ id: 'x-ai/grok-3' })).toBe('[xAI]');
		expect(getProviderTag({ id: 'baidu/ernie-4.5' })).toBe('[Baidu]');
	});

	it('resolves provider tag directly from URL', () => {
		expect(resolveProviderTagFromUrl('https://openrouter.ai/api/v1')).toBe('[OpnR]');
		expect(resolveProviderTagFromUrl('https://generativelanguage.googleapis.com/v1beta')).toBe(
			'[GVrtx]'
		);
		expect(resolveProviderTagFromUrl('')).toBeNull();
	});

	it('formats model label with provider prefix without duplicating', () => {
		const tierModel = { id: 'agent-runtime-free-default', owned_by: 'agent-runtime' };
		expect(formatModelNameWithProvider('Free Models Router', tierModel)).toBe(
			'[WeAI] Free Models Router'
		);
		expect(formatModelNameWithProvider('[WeAI] Free Models Router', tierModel)).toBe(
			'[WeAI] Free Models Router'
		);

		const openRouterModel = { id: 'google/gemini-2.5-flash', url: 'https://openrouter.ai/api/v1' };
		expect(formatModelNameWithProvider('Gemini 2.5 Flash', openRouterModel)).toBe(
			'[OpnR] Gemini 2.5 Flash'
		);
	});
});
