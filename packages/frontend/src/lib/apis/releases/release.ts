import { WEAI_API_BASE_URL } from '$lib/constants';

export type Release = {
	version: string;
	released_at: string | null;
	sections: Record<string, string[]>;
};

export async function getRelease(token: string): Promise<Release> {
	const response = await fetch(`${WEAI_API_BASE_URL}/agent-runtime/release`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (!response.ok) throw new Error('Could not load WeAI release notes');
	return response.json();
}
