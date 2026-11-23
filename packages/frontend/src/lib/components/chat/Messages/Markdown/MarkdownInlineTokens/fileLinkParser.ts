export type FileLinkSource = 'agent-runtime-document' | 'file';

export type FileLinkRef = {
	source: FileLinkSource;
	fileId: string;
	revision: number | null;
	href: string;
};

export type TextSegment =
	| { type: 'text'; text: string }
	| { type: 'file'; ref: FileLinkRef };

const AGENT_DOC_PATH_PATTERN = /^\/api\/v1\/agent-runtime\/documents\/([^/?#]+)\/content\/?$/;
const FILE_PATH_PATTERN = /^\/api\/v1\/files\/([^/?#]+)\/content\/?$/;
const RAW_FILE_URL_REGEX =
	/(?:https?:\/\/[^\s<>"'`)]+)?\/api\/v1\/(?:agent-runtime\/documents|files)\/([A-Za-z0-9_-]+)\/content(?:\?[^\s<>"'`)\],;]+)?/g;

export function parseFileLinkUrl(href: string): FileLinkRef | null {
	if (!href || typeof href !== 'string') return null;
	const trimmed = href.trim();
	try {
		const base = typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
		const parsed = new URL(trimmed, base);
		if (typeof window !== 'undefined' && /^https?:\/\//i.test(trimmed)) {
			// Allow same-origin or loopback dev backend (e.g., 127.0.0.1:8080 vs 127.0.0.1:5173)
			const sameHost = parsed.hostname === window.location.hostname;
			if (!sameHost) return null;
		}
		const docMatch = parsed.pathname.match(AGENT_DOC_PATH_PATTERN);
		if (docMatch) {
			const fileId = decodeURIComponent(docMatch[1]);
			const revParam = parsed.searchParams.get('revision');
			const parsedRev = revParam !== null ? Number(revParam) : null;
			const revision =
				parsedRev !== null && Number.isInteger(parsedRev) && parsedRev > 0 ? parsedRev : null;
			return {
				source: 'agent-runtime-document',
				fileId,
				revision,
				href: trimmed
			};
		}
		const fileMatch = parsed.pathname.match(FILE_PATH_PATTERN);
		if (fileMatch) {
			return {
				source: 'file',
				fileId: decodeURIComponent(fileMatch[1]),
				revision: null,
				href: trimmed
			};
		}
		return null;
	} catch {
		return null;
	}
}

export function splitTextWithFileLinks(raw: string): TextSegment[] {
	if (
		!raw ||
		(!raw.includes('/api/v1/agent-runtime/documents/') && !raw.includes('/api/v1/files/'))
	) {
		return [{ type: 'text', text: raw }];
	}

	const segments: TextSegment[] = [];
	let lastIndex = 0;
	RAW_FILE_URL_REGEX.lastIndex = 0;

	let match: RegExpExecArray | null;
	while ((match = RAW_FILE_URL_REGEX.exec(raw)) !== null) {
		const matchedUrl = match[0];
		const cleaned = matchedUrl.replace(/[.,;:!?]+$/, '');
		const ref = parseFileLinkUrl(cleaned);
		if (!ref) {
			continue;
		}
		if (match.index > lastIndex) {
			segments.push({ type: 'text', text: raw.slice(lastIndex, match.index) });
		}
		segments.push({ type: 'file', ref });
		lastIndex = match.index + cleaned.length;
	}

	if (lastIndex === 0) {
		return [{ type: 'text', text: raw }];
	}
	if (lastIndex < raw.length) {
		segments.push({ type: 'text', text: raw.slice(lastIndex) });
	}
	return segments;
}
