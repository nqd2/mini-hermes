import { writable } from 'svelte/store';
import { WEAI_API_BASE_URL } from '$lib/constants';

export type ProviderIconEntry = {
	url: string;
	invert_dark?: boolean;
};

export type ResolvedModelIcon = {
	src: string;
	fallback: string;
	invertDark: boolean;
};

const WEAI_LOGO = '/favicon.png';

const LOCAL_PROVIDER_ICONS: Record<string, { url: string; invertDark?: boolean }> = {
	openai: { url: '/provider-icons/openai.svg', invertDark: true },
	anthropic: { url: '/provider-icons/anthropic.svg', invertDark: true },
	google: { url: '/provider-icons/google.svg' },
	'google-vertex': { url: '/provider-icons/google.svg' },
	perplexity: { url: '/provider-icons/perplexity.svg', invertDark: true },
	mistral: { url: '/provider-icons/mistral.png' },
	mistralai: { url: '/provider-icons/mistral.png' },
	deepseek: { url: '/provider-icons/deepseek.png' },
	cohere: { url: '/provider-icons/cohere.png' },
	nvidia: { url: '/provider-icons/nvidia.png' },
	xai: { url: '/provider-icons/xai.png' },
	'x-ai': { url: '/provider-icons/xai.png' },
	qwen: { url: '/provider-icons/qwen.png' },
	alibaba: { url: '/provider-icons/qwen.png' },
	meta: { url: '/provider-icons/meta.png' },
	'meta-llama': { url: '/provider-icons/meta.png' },
	microsoft: { url: '/provider-icons/microsoft.svg' },
	'particle-ai': { url: '/provider-icons/particle.svg' },
	baidu: { url: '/provider-icons/baidu.png' },
	moonshotai: { url: '/provider-icons/moonshotai.png' },
	minimax: { url: '/provider-icons/minimax.png' }
};

const SLUG_ALIASES: Record<string, string[]> = {
	google: ['google', 'google-vertex', 'google-ai-studio'],
	mistralai: ['mistralai', 'mistral'],
	'x-ai': ['x-ai', 'xai'],
	qwen: ['qwen', 'alibaba'],
	'meta-llama': ['meta-llama', 'meta']
};

export function isLogicalTierModel(modelId: string | null | undefined): boolean {
	if (!modelId) return false;
	return modelId.startsWith('agent-runtime-free-') || modelId.startsWith('agent-runtime-plus-');
}

export function extractProviderSlug(modelId: string | null | undefined): string | null {
	if (!modelId || typeof modelId !== 'string') return null;
	const trimmed = modelId.trim().toLowerCase().replace(/^~+/, '');
	if (!trimmed || isLogicalTierModel(trimmed)) return null;

	// Strip optional prefix or `:free` suffix before checking slash
	const slashIdx = trimmed.indexOf('/');
	if (slashIdx > 0) {
		return trimmed.slice(0, slashIdx);
	}

	// Unprefixed direct model IDs
	if (
		/^(gpt-|o1\b|o3\b|o4\b|chatgpt-|dall-e|whisper-|tts-|text-embedding-|omni-moderation|codex-|babbage-|davinci-)/.test(
			trimmed
		)
	) {
		return 'openai';
	}
	if (trimmed.startsWith('claude-')) return 'anthropic';
	if (trimmed.startsWith('gemini-') || trimmed.startsWith('gemma-')) return 'google';
	if (trimmed.startsWith('deepseek-')) return 'deepseek';
	if (trimmed.startsWith('qwen')) return 'qwen';
	if (trimmed.startsWith('mistral') || trimmed.startsWith('mixtral') || trimmed.startsWith('codestral')) {
		return 'mistral';
	}
	if (trimmed.startsWith('grok-')) return 'xai';
	if (trimmed.startsWith('command-')) return 'cohere';
	if (trimmed.startsWith('sonar')) return 'perplexity';

	return null;
}

export function resolveModelIcon(
	modelId: string | null | undefined,
	userRole: string | null | undefined,
	remoteIcons: Record<string, ProviderIconEntry> = {}
): ResolvedModelIcon {
	if (userRole !== 'admin' || !modelId || isLogicalTierModel(modelId)) {
		return { src: WEAI_LOGO, fallback: WEAI_LOGO, invertDark: false };
	}

	const slug = extractProviderSlug(modelId);
	if (!slug) {
		return { src: WEAI_LOGO, fallback: WEAI_LOGO, invertDark: false };
	}

	const candidates = SLUG_ALIASES[slug] ?? [slug];
	let localEntry: { url: string; invertDark?: boolean } | undefined;
	for (const key of candidates) {
		if (LOCAL_PROVIDER_ICONS[key]) {
			localEntry = LOCAL_PROVIDER_ICONS[key];
			break;
		}
	}

	for (const key of candidates) {
		const remote = remoteIcons[key];
		if (remote?.url) {
			return {
				src: remote.url,
				fallback: localEntry?.url ?? WEAI_LOGO,
				invertDark: Boolean(remote.invert_dark)
			};
		}
	}

	if (localEntry) {
		return {
			src: localEntry.url,
			fallback: WEAI_LOGO,
			invertDark: Boolean(localEntry.invertDark)
		};
	}

	return { src: WEAI_LOGO, fallback: WEAI_LOGO, invertDark: false };
}

export const openRouterProviderIcons = writable<Record<string, ProviderIconEntry>>({});

let loadStarted = false;

export async function loadOpenRouterProviderIcons(token: string | null | undefined): Promise<void> {
	if (loadStarted || !token) return;
	loadStarted = true;
	try {
		const response = await fetch(`${WEAI_API_BASE_URL}/agent-runtime/admin/provider-icons`, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!response.ok) return;
		const data = await response.json();
		if (data?.icons && typeof data.icons === 'object') {
			openRouterProviderIcons.set(data.icons);
		}
	} catch {
		// Fail silently; local fallback icons and WeAI logo remain active.
	}
}
