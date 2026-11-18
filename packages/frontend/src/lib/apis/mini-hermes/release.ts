import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MhRelease = {
	version: string;
	released_at: string | null;
	sections: Record<string, string[]>;
};

export async function getMhRelease(token: string): Promise<MhRelease> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/mini-hermes/release`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (!response.ok) throw new Error('Could not load Mini-Hermes release notes');
	return response.json();
}
