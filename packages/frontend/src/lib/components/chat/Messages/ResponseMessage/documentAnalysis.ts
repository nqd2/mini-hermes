import { WEAI_BASE_URL } from '$lib/constants';
import type { TracePage } from './documentAnalysisTrace';

export type AnalysisReceipt = {
	schema_version: 1;
	task_id: string;
	revision: number;
	phase: string;
	status: string;
	progress: { completed_units: number; total_units: number };
};

type Evidence = { id: string; locator: string; kind: string; text: string; confidence?: number[] };
export type AnalysisPage = {
	profile: Record<string, unknown> | null;
	gaps: { locator: string; reason: string }[];
	evidence: Evidence[];
	next_offset: number | null;
};

export type AnalysisStatus = Omit<AnalysisReceipt, 'schema_version'> & {
	result: string | null;
	error: string | null;
	cancel_requested: boolean;
	analysis?: AnalysisPage;
	trace?: TracePage;
};

export const analysisActive = (status: string) => status === 'pending' || status === 'processing';

export async function readAnalysis(
	taskId: string,
	signal: AbortSignal,
	evidenceOffset?: number,
	traceAfter?: number
): Promise<AnalysisStatus> {
	const query =
		evidenceOffset === undefined
			? 'include_analysis=false&evidence_limit=1'
			: `evidence_offset=${evidenceOffset}&evidence_limit=10`;
	const traceQuery = traceAfter === undefined ? '' : `&trace_after=${traceAfter}`;
	const res = await fetch(
		`${WEAI_BASE_URL}/api/v1/agent-runtime/tasks/${encodeURIComponent(taskId)}?${query}${traceQuery}`,
		{ headers: { Authorization: `Bearer ${localStorage.token}` }, signal }
	);
	if (!res.ok) throw new Error(String(res.status));
	return res.json();
}

export async function changeAnalysis(taskId: string, action: 'cancel' | 'resume', signal: AbortSignal) {
	const res = await fetch(
		`${WEAI_BASE_URL}/api/v1/agent-runtime/tasks/${encodeURIComponent(taskId)}/${action}`,
		{ method: 'POST', headers: { Authorization: `Bearer ${localStorage.token}` }, signal }
	);
	if (!res.ok) throw new Error(String(res.status));
}
