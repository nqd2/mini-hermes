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

	it('formats workspace agent tool names to Title Case', () => {
		expect(formatToolDisplayName('run_workspace_command')).toBe('Run Workspace Command');
		expect(formatToolDisplayName('list_workspace_files')).toBe('List Workspace Files');
		expect(formatToolDisplayName('read_workspace_file')).toBe('Read Workspace File');
		expect(formatToolDisplayName('write_workspace_file')).toBe('Write Workspace File');
		expect(formatToolDisplayName('edit_workspace_file')).toBe('Edit Workspace File');
		expect(formatToolDisplayName('search_workspace_files')).toBe('Search Workspace Files');
		expect(formatToolDisplayName('run_workspace_subtasks')).toBe('Run Workspace Subtasks');
	});

	it('formats kebab-case and preserves acronyms', () => {
		expect(formatToolDisplayName('read-MCP-tool')).toBe('Read MCP Tool');
		expect(formatToolDisplayName('calculator')).toBe('Calculator');
		expect(formatToolDisplayName('')).toBe('Tool');
		expect(formatToolDisplayName(undefined)).toBe('Tool');
	});
});
