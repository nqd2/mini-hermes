import { describe, expect, it } from 'vitest';
import { getMaterialIconName, getMaterialIconPath } from './materialFileIcon';

describe('materialFileIcon', () => {
	it('maps Office, archive, code, and media extensions to Material Icon Theme icons', () => {
		expect(getMaterialIconName('bao_cao_free.docx')).toBe('word');
		expect(getMaterialIconName('budget.XLSX')).toBe('table');
		expect(getMaterialIconName('slides.pptx')).toBe('powerpoint');
		expect(getMaterialIconName('manual.pdf')).toBe('pdf');
		expect(getMaterialIconName('hello_world.zip')).toBe('zip');
		expect(getMaterialIconName('README.md')).toBe('markdown');
		expect(getMaterialIconName('script.py')).toBe('python');
		expect(getMaterialIconName('photo.png')).toBe('image');
		expect(getMaterialIconName('diagram.svg')).toBe('svg');
	});

	it('falls back to document icon for unknown or extensionless files', () => {
		expect(getMaterialIconName('Makefile')).toBe('document');
		expect(getMaterialIconName('archive.unknownext')).toBe('document');
		expect(getMaterialIconName('')).toBe('document');
		expect(getMaterialIconPath('report.docx')).toBe('/material-icons/word.svg');
	});
});
