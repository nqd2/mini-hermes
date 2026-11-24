import { describe, expect, it } from 'vitest';
import { extractProviderSlug, isLogicalTierModel, resolveModelIcon } from './providerIcons';

describe('providerIcons', () => {
	it('identifies logical tier models and always uses WeAI logo for non-admin users', () => {
		expect(isLogicalTierModel('agent-runtime-free')).toBe(true);
		expect(isLogicalTierModel('agent-runtime-free-small')).toBe(true);
		expect(isLogicalTierModel('agent-runtime-plus-large')).toBe(true);
		expect(isLogicalTierModel('openai/gpt-4o')).toBe(false);

		expect(resolveModelIcon('openai/gpt-4o', 'user', { openai: { url: 'https://openrouter.ai/images/icons/OpenAI.svg' } })).toEqual({
			src: '/favicon.png',
			fallback: '/favicon.png',
			invertDark: false
		});
		expect(resolveModelIcon('agent-runtime-free', 'admin', {})).toEqual({
			src: '/favicon.png',
			fallback: '/favicon.png',
			invertDark: false
		});
		expect(resolveModelIcon('agent-runtime-free-small', 'admin', {})).toEqual({
			src: '/favicon.png',
			fallback: '/favicon.png',
			invertDark: false
		});
	});

	it('extracts provider slugs for both namespaced and direct model IDs', () => {
		expect(extractProviderSlug('google/gemma-4-31b-it:free')).toBe('google');
		expect(extractProviderSlug('anthropic/claude-3.5-sonnet')).toBe('anthropic');
		expect(extractProviderSlug('~anthropic/claude-fable-latest')).toBe('anthropic');
		expect(extractProviderSlug('~deepseek/deepseek-flash-latest')).toBe('deepseek');
		expect(extractProviderSlug('~moonshotai/kimi-latest')).toBe('moonshotai');
		expect(extractProviderSlug('gpt-4o-mini')).toBe('openai');
		expect(extractProviderSlug('omni-moderation-latest')).toBe('openai');
		expect(extractProviderSlug('claude-3-opus')).toBe('anthropic');
		expect(extractProviderSlug('deepseek-r1')).toBe('deepseek');
		expect(extractProviderSlug('qwen/qwen-2.5-72b')).toBe('qwen');
	});

	it('prefers cached OpenRouter icon for admin with local fallback, then WeAI logo', () => {
		const remote = {
			openai: { url: 'https://openrouter.ai/images/icons/OpenAI.svg', invert_dark: true },
			'custom-lab': { url: 'https://openrouter.ai/images/icons/Custom.svg', invert_dark: false }
		};
		expect(resolveModelIcon('gpt-4o', 'admin', remote)).toEqual({
			src: 'https://openrouter.ai/images/icons/OpenAI.svg',
			fallback: '/provider-icons/openai.svg',
			invertDark: true
		});
		expect(resolveModelIcon('custom-lab/model-1', 'admin', remote)).toEqual({
			src: 'https://openrouter.ai/images/icons/Custom.svg',
			fallback: '/favicon.png',
			invertDark: false
		});
		expect(resolveModelIcon('google/gemma-4-31b-it:free', 'admin', {})).toEqual({
			src: '/provider-icons/google.svg',
			fallback: '/favicon.png',
			invertDark: false
		});
		expect(resolveModelIcon('unknown-vendor/model-x', 'admin', {})).toEqual({
			src: '/favicon.png',
			fallback: '/favicon.png',
			invertDark: false
		});
	});
});
