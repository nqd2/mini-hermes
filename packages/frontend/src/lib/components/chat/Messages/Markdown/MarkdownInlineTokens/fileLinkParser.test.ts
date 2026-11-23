import { describe, expect, it } from 'vitest';
import { parseFileLinkUrl, splitTextWithFileLinks } from './fileLinkParser';

describe('fileLinkParser', () => {
	it('parses relative agent-runtime document content URLs with revision', () => {
		expect(
			parseFileLinkUrl(
				'/api/v1/agent-runtime/documents/239d27d8-c547-47cb-9430-8f08f175cdcd/content?revision=1'
			)
		).toEqual({
			source: 'agent-runtime-document',
			fileId: '239d27d8-c547-47cb-9430-8f08f175cdcd',
			revision: 1,
			href: '/api/v1/agent-runtime/documents/239d27d8-c547-47cb-9430-8f08f175cdcd/content?revision=1'
		});
	});

	it('parses general /api/v1/files/<id>/content URLs', () => {
		expect(parseFileLinkUrl('/api/v1/files/file-abc/content')).toEqual({
			source: 'file',
			fileId: 'file-abc',
			revision: null,
			href: '/api/v1/files/file-abc/content'
		});
	});

	it('returns null for non-file URLs', () => {
		expect(parseFileLinkUrl('https://example.com/file.docx')).toBeNull();
		expect(parseFileLinkUrl('/notes/abc')).toBeNull();
		expect(parseFileLinkUrl('/api/v1/agent-runtime/documents/doc-123/revisions')).toBeNull();
	});

	it('splits plain text containing raw file URLs and preserves trailing punctuation', () => {
		const raw =
			'Tải file: /api/v1/agent-runtime/documents/239d27d8-c547-47cb-9430-8f08f175cdcd/content?revision=2. Cảm ơn!';
		const segments = splitTextWithFileLinks(raw);
		expect(segments).toEqual([
			{ type: 'text', text: 'Tải file: ' },
			{
				type: 'file',
				ref: {
					source: 'agent-runtime-document',
					fileId: '239d27d8-c547-47cb-9430-8f08f175cdcd',
					revision: 2,
					href: '/api/v1/agent-runtime/documents/239d27d8-c547-47cb-9430-8f08f175cdcd/content?revision=2'
				}
			},
			{ type: 'text', text: '. Cảm ơn!' }
		]);
	});

	it('leaves ordinary text untouched', () => {
		expect(splitTextWithFileLinks('Xin chào thế giới')).toEqual([
			{ type: 'text', text: 'Xin chào thế giới' }
		]);
	});
});
