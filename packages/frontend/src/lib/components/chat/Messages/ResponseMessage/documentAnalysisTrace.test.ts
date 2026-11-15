import { describe, expect, it } from 'vitest';
import { mergeTrace, type TraceEvent } from './documentAnalysisTrace';

describe('document analysis trace', () => {
	it('merges live output on the same step and ignores replayed events', () => {
		const rows: TraceEvent[] = [
			{ sequence: 1, step_id: 'scan', kind: 'started', data: { phase: 'survey', code: 'read()' } },
			{ sequence: 2, step_id: 'scan', kind: 'stdout', data: { text: 'sheet 1\n' } }
		];
		const current = mergeTrace({ after: 0, steps: [] }, rows);
		const result = mergeTrace(current, [
			rows[1],
			{ sequence: 3, step_id: 'scan', kind: 'stderr', data: { text: 'cell unavailable' } },
			{ sequence: 4, step_id: 'scan', kind: 'finished', data: { status: 'failed', truncated: [] } }
		]);
		expect(result.after).toBe(4);
		expect(result.steps).toHaveLength(1);
		expect(result.steps[0]).toMatchObject({ code: 'read()', stdout: 'sheet 1\n', stderr: 'cell unavailable', status: 'failed' });
	});
});
