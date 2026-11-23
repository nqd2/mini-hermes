import { extractProviderSlug, isLogicalTierModel } from './providerIcons';

const SLUG_TO_TAG: Record<string, string> = {
	weai: '[WeAI]',
	'agent-runtime': '[WeAI]',
	openrouter: '[OpnR]',
	openai: '[OpnAI]',
	anthropic: '[Anth]',
	google: '[GVrtx]',
	'google-vertex': '[GVrtx]',
	'google-ai-studio': '[GVrtx]',
	vertex: '[GVrtx]',
	'1min': '[1Min]',
	'1minai': '[1Min]',
	deepseek: '[Dpsk]',
	mistral: '[Mist]',
	mistralai: '[Mist]',
	xai: '[xAI]',
	'x-ai': '[xAI]',
	cohere: '[Coh]',
	perplexity: '[Prpx]',
	groq: '[Groq]',
	qwen: '[Qwen]',
	alibaba: '[Qwen]',
	meta: '[Meta]',
	'meta-llama': '[Meta]',
	baidu: '[Baidu]',
	moonshotai: '[Mnst]',
	moonshot: '[Mnst]',
	minimax: '[Mnmx]',
	microsoft: '[MSFT]',
	nvidia: '[NVDA]',
	together: '[Tgth]',
	lmstudio: '[LMS]',
	'llama.cpp': '[Lcpp]'
};

const URL_PATTERNS: Array<{ test: RegExp; tag: string }> = [
	{ test: /openrouter\.ai/i, tag: '[OpnR]' },
	{ test: /api\.openai\.com/i, tag: '[OpnAI]' },
	{ test: /anthropic\.com/i, tag: '[Anth]' },
	{ test: /(aiplatform\.googleapis\.com|generativelanguage\.googleapis\.com|vertex)/i, tag: '[GVrtx]' },
	{ test: /1min\.ai/i, tag: '[1Min]' },
	{ test: /deepseek\.com/i, tag: '[Dpsk]' },
	{ test: /mistral\.ai/i, tag: '[Mist]' },
	{ test: /x\.ai/i, tag: '[xAI]' },
	{ test: /cohere\.(com|ai)/i, tag: '[Coh]' },
	{ test: /perplexity\.ai/i, tag: '[Prpx]' },
	{ test: /groq\.com/i, tag: '[Groq]' },
	{ test: /together\.(xyz|ai)/i, tag: '[Tgth]' },
	{ test: /dashscope\.aliyuncs\.com/i, tag: '[Qwen]' },
	{ test: /moonshot\.(cn|ai)/i, tag: '[Mnst]' },
	{ test: /minimax/i, tag: '[Mnmx]' },
	{ test: /baidu(bce)?\.com/i, tag: '[Baidu]' }
];

export function resolveProviderTagFromUrl(url: string | null | undefined): string | null {
	if (!url || typeof url !== 'string') return null;
	const trimmed = url.trim();
	if (!trimmed) return null;
	if (trimmed.startsWith('weai://')) return '[WeAI]';

	for (const { test, tag } of URL_PATTERNS) {
		if (test.test(trimmed)) {
			return tag;
		}
	}
	return null;
}

export function getProviderTag(model?: any, fallbackModelId?: string | null): string {
	const modelId: string =
		(typeof model === 'string' ? model : model?.id ?? model?.value ?? fallbackModelId ?? '') || '';
	const normalizedId = String(modelId).trim();

	if (
		isLogicalTierModel(normalizedId) ||
		model?.owned_by === 'agent-runtime' ||
		normalizedId.toLowerCase().startsWith('agent-runtime-')
	) {
		return '[WeAI]';
	}

	const rawUrl =
		typeof model === 'object' && model !== null
			? model.url ?? model.base_url ?? model.openai?.url ?? null
			: null;
	const urlTag = resolveProviderTagFromUrl(rawUrl);
	if (urlTag) {
		return urlTag;
	}

	const explicitProvider =
		typeof model === 'object' && model !== null && typeof model.provider === 'string'
			? model.provider.trim().toLowerCase()
			: '';
	if (explicitProvider && SLUG_TO_TAG[explicitProvider]) {
		return SLUG_TO_TAG[explicitProvider];
	}

	const slug = extractProviderSlug(normalizedId);
	if (slug && SLUG_TO_TAG[slug]) {
		return SLUG_TO_TAG[slug];
	}

	return '';
}

export function formatModelNameWithProvider(
	label: string | null | undefined,
	model?: any,
	fallbackModelId?: string | null
): string {
	const cleanLabel = (label ?? '').trim();
	if (!cleanLabel) return '';
	const tag = getProviderTag(model, fallbackModelId);
	if (!tag) return cleanLabel;
	if (cleanLabel.startsWith(tag)) return cleanLabel;
	return `${tag} ${cleanLabel}`;
}
