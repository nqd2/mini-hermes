import { describe, expect, it } from 'vitest';
import { findPendingToolApproval } from './chatToolApproval';

const message = (id: string, userId: string | undefined, output: Record<string, any>[]) => ({
	id,
	user_id: userId,
	role: 'assistant',
	output
});

const toolCall = (callId: string, status = 'pending', name = 'write_file') => ({
	type: 'function_call',
	name,
	call_id: callId,
	status
});

describe('pending tool approval selection', () => {
	it('selects the newest unresolved non-ask-user call from the current user', () => {
		const newest = message('message-new', 'user-1', [
			toolCall('resolved', 'requires_approval'),
			{ type: 'function_call_output', call_id: 'resolved' },
			toolCall('ask-user', 'pending', 'ask_user')
		]);
		const older = message('message-old', 'user-1', [toolCall('write-call', 'requires_approval')]);

		expect(findPendingToolApproval([newest, older], 'user-1', 'user-1')).toEqual({
			message: older,
			callId: 'write-call'
		});
	});

	it('skips messages owned by another user and uses the chat owner for legacy messages', () => {
		const foreign = message('foreign', 'user-2', [toolCall('foreign-call')]);
		const legacy = message('legacy', undefined, [toolCall('legacy-call')]);

		expect(findPendingToolApproval([foreign, legacy], 'user-1', 'user-1')).toEqual({
			message: legacy,
			callId: 'legacy-call'
		});
	});

	it('returns null when calls are not pending, have no identifier, or lack a message id', () => {
		const resolved = message('done', 'user-1', [toolCall('done-call', 'completed')]);
		const missingCallId = message('missing-call', 'user-1', [toolCall('')]);
		const missingMessageId = message('', 'user-1', [toolCall('pending-call')]);

		expect(
			findPendingToolApproval([resolved, missingCallId, missingMessageId], 'user-1')
		).toBeNull();
	});
});
