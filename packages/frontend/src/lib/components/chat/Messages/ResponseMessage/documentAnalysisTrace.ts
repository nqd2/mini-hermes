type WorkerStatus = 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
type StepStart = {
	phase: string;
	code?: string;
	function?: string;
	source_version?: string;
	code_truncated?: boolean;
	scope?: { format?: string; first_evidence?: number; last_evidence?: number; notes?: number };
};

export type TraceEvent = { sequence: number; step_id: string } & (
	| { kind: 'started'; data: StepStart }
	| { kind: 'stdout' | 'stderr'; data: { text: string } }
	| { kind: 'finished'; data: { status: Exclude<WorkerStatus, 'running'>; elapsed_ms?: number; truncated: string[] } }
);

export type TracePage = { events: TraceEvent[]; next_after: number; has_more: boolean };
export type TraceStep = StepStart & {
	id: string;
	status: WorkerStatus;
	stdout: string;
	stderr: string;
	elapsed_ms?: number;
	truncated: string[];
};

export type TraceState = { after: number; steps: TraceStep[] };

export function mergeTrace(state: TraceState, events: TraceEvent[]): TraceState {
	const steps = new Map(state.steps.map((step) => [step.id, step]));
	let after = state.after;
	for (const event of events) {
		if (event.sequence <= after) continue;
		after = event.sequence;
		const step = steps.get(event.step_id);
		if (event.kind === 'started' && !step) {
			steps.set(event.step_id, { ...event.data, id: event.step_id, status: 'running', stdout: '', stderr: '', truncated: [] });
		} else if (step && (event.kind === 'stdout' || event.kind === 'stderr')) {
			steps.set(step.id, { ...step, [event.kind]: step[event.kind] + event.data.text });
		} else if (step && event.kind === 'finished') {
			steps.set(step.id, { ...step, ...event.data });
		}
	}
	return { after, steps: [...steps.values()] };
}
