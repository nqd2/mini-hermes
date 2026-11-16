import { describe, expect, it } from 'vitest';
import { formatToolDisplayName } from './toolName';

describe('formatToolDisplayName', () => {
	it('formats snake_case tool names to Title Case', () => {
		expect(formatToolDisplayName('start_document_summary_task')).toBe(
			'Start Document Summary Task'
		);
		expect(formatToolDisplayName('list_editable_documents')).toBe('List Editable Documents');
		expect(formatToolDisplayName('get_document_task')).toBe('Get Document Task');
	});

	it('formats kebab-case and preserves acronyms', () => {
		expect(formatToolDisplayName('read-MCP-tool')).toBe('Read MCP Tool');
		expect(formatToolDisplayName('calculator')).toBe('Calculator');
		expect(formatToolDisplayName('')).toBe('Tool');
		expect(formatToolDisplayName(undefined)).toBe('Tool');
	});
});
