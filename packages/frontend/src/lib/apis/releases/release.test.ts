import { afterEach, describe, expect, it, vi } from 'vitest';
import { getRelease } from './release';
import { shouldShowRelease } from '../../utils/releaseVisibility';
import modal from '../../components/ReleaseModal.svelte?raw';
import layout from '../../../routes/(app)/+layout.svelte?raw';
import about from '../../components/chat/Settings/About.svelte?raw';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('WeAI release notes', () => {
	it('shows a new version once when enabled', () => {
		expect(shouldShowRelease('1.0.0', undefined, true)).toBe(true);
		expect(shouldShowRelease('1.0.0', '1.0.0', true)).toBe(false);
		expect(shouldShowRelease('1.0.1', '1.0.0', true)).toBe(true);
		expect(shouldShowRelease('1.0.1', undefined, false)).toBe(false);
		expect(shouldShowRelease('dev', undefined, true)).toBe(false);
	});

	it('loads only the local release endpoint', async () => {
		const release = { version: 'dev', released_at: null, sections: {} };
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => release });
		vi.stubGlobal('fetch', fetch);
		await expect(getRelease('test-token')).resolves.toEqual(release);
		expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/v1\/agent-runtime\/release$/), {
			headers: { Authorization: 'Bearer test-token' }
		});
	});

	it('renders plain text and preserves About attribution', () => {
		expect(modal).toContain('<li>{item}</li>');
		expect(modal).not.toContain('@html');
		expect(layout).not.toMatch(/getVersionUpdates|UpdateInfoToast/);
		expect(about).toContain('<BuildVersion />');
		expect(about).toContain('https://github.com/open-webui/open-webui');
		expect(about).not.toContain('/releases/tag/');
	});
});
