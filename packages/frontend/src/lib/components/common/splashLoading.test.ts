import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const frontendRoot = resolve(import.meta.dirname, '../../../..');

describe('brand splash and large loader integration', () => {
	it('provides the self-contained animated SVG asset at static/static/splash-loading.svg', () => {
		const svgPath = resolve(frontendRoot, 'static/static/splash-loading.svg');
		expect(existsSync(svgPath)).toBe(true);

		const svg = readFileSync(svgPath, 'utf8');
		expect(svg).toContain('viewBox="0 0 420 240"');
		expect(svg).toContain('@keyframes loadDown');
		expect(svg).toContain('@keyframes loadUp');
		expect(svg).toContain('prefers-reduced-motion:reduce');
	});

	it('wires app.html splash screen to /static/splash-loading.svg with dark-mode glow', () => {
		const appHtml = readFileSync(resolve(frontendRoot, 'src/app.html'), 'utf8');
		expect(appHtml).toContain("const preloadHref = '/static/splash-loading.svg';");
		expect(appHtml).toContain("logo.src = '/static/splash-loading.svg';");
		expect(appHtml).toContain('src="/static/splash-loading.svg"');
		expect(appHtml).toContain('html.dark #logo');
		expect(appHtml).not.toContain('splash-dark.png');
		expect(appHtml).not.toContain('/static/splash.png');
		expect(appHtml).not.toContain('filter: invert(1)');
	});

	it('supports variant="brand" and page/panel sizes in Spinner.svelte', () => {
		const spinner = readFileSync(
			resolve(frontendRoot, 'src/lib/components/common/Spinner.svelte'),
			'utf8'
		);
		expect(spinner).toContain("export let variant: 'spinner' | 'brand' = 'spinner';");
		expect(spinner).toContain("export let size: 'panel' | 'page' = 'panel';");
		expect(spinner).toContain('src="/static/splash-loading.svg"');
		expect(spinner).toContain("size === 'page' ? 'h-20 w-auto' : 'h-12 w-auto'");
		expect(spinner).toContain('dark:drop-shadow-');
	});
});
