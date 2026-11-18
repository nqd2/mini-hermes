import { afterEach, describe, expect, it, vi } from 'vitest';
import { getMhRelease } from './release';
import { shouldShowMhRelease } from '../../utils/mhReleaseVisibility';
import modal from '../../components/MhReleaseModal.svelte?raw';
import layout from '../../../routes/(app)/+layout.svelte?raw';
import about from '../../components/chat/Settings/About.svelte?raw';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('Mini-Hermes release notes', () => {
	it('shows a new version once when enabled', () => {
		expect(shouldShowMhRelease('1.0.0', undefined, true)).toBe(true);
		expect(shouldShowMhRelease('1.0.0', '1.0.0', true)).toBe(false);
		expect(shouldShowMhRelease('1.0.1', '1.0.0', true)).toBe(true);
		expect(shouldShowMhRelease('1.0.1', undefined, false)).toBe(false);
		expect(shouldShowMhRelease('dev', undefined, true)).toBe(false);
	});

	it('loads only the local release endpoint', async () => {
		const release = { version: 'dev', released_at: null, sections: {} };
		const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => release });
		vi.stubGlobal('fetch', fetch);
		await expect(getMhRelease('test-token')).resolves.toEqual(release);
		expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/v1\/mini-hermes\/release$/), {
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
