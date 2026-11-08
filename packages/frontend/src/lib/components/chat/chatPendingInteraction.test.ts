import { describe, expect, it } from 'vitest';
import {
	findPendingAskUser,
	getPendingAskUserFromMessage,
	messageHasPendingAskUser,
	parseToolArguments
} from './chatPendingInteraction';

const askUserMessage = (id: string, callId: string, userId = 'user-1', status = 'pending') => ({
	id,
	user_id: userId,
	role: 'assistant',
	output: [
		{
			type: 'function_call',
			name: 'ask_user',
			status,
			call_id: callId,
			arguments: JSON.stringify(JSON.stringify({ questions: [{ id: 'q1' }] }))
		}
	]
});

describe('chat pending interactions', () => {
	it('parses nested JSON object arguments and rejects arrays', () => {
		expect(parseToolArguments('{"questions":[]}')).toEqual({ questions: [] });
		expect(parseToolArguments('"{\\"questions\\":[]}"')).toEqual({
			questions: []
		});
		expect(parseToolArguments('[1,2]')).toEqual({});
		expect(parseToolArguments('invalid')).toEqual({});
	});

	it('selects the newest pending ask-user call from the active history list', () => {
		const older = askUserMessage('message-1', 'call-1');
		const newer = askUserMessage('message-2', 'call-2');

		const pending = findPendingAskUser([older, newer], 'user-1', 'user-1');

		expect(pending).toMatchObject({
			message: newer,
			call: newer.output[0],
			args: { questions: [{ id: 'q1' }] }
		});
	});

	it('ignores another owner, resolved calls, and non assistant messages', () => {
		const otherUser = askUserMessage('other', 'call-other', 'user-2');
		const resolved = askUserMessage('resolved', 'call-done', 'user-1', 'completed');
		const nonAssistant = {
			...askUserMessage('user-message', 'call-user'),
			role: 'user'
		};

		expect(findPendingAskUser([otherUser, resolved, nonAssistant], 'user-1', 'user-1')).toBeNull();
	});

	it('reports whether an individual message still has a pending ask-user call', () => {
		expect(messageHasPendingAskUser(askUserMessage('message-1', 'call-1'))).toBe(true);
		expect(
			messageHasPendingAskUser(askUserMessage('message-2', 'call-2', 'user-1', 'rejected'))
		).toBe(false);
		expect(getPendingAskUserFromMessage({ role: 'assistant', output: [] })).toBeNull();
	});
});
