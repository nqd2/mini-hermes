import type { ChatInteraction } from './chatInteractionQueue';

export type ChatInteractionEvent = {
	chat_id?: string | null;
	message_id?: string;
	data?: {
		type?: string;
		data?: Record<string, any> | null;
	};
};

type InteractionQueue = {
	enqueue: (interaction: ChatInteraction) => void;
	remove: (interactionId: string | null | undefined) => boolean;
};

type Dependencies = {
	currentChatId: string | null;
	approvalMode: string | null;
	isTemporaryChat: (chatId: string | null | undefined) => boolean;
	queue: InteractionQueue;
	createId: () => string;
	onInteractionDone: (interactionId: unknown) => void;
};

/** Routes browser tool-interaction events before other chat-message event handling. */
export const handleChatToolInteractionEvent = (
	event: ChatInteractionEvent,
	callback: ((value: any) => void) | null | undefined,
	{ currentChatId, approvalMode, isTemporaryChat, queue, createId, onInteractionDone }: Dependencies
): boolean => {
	const type = event?.data?.type;
	const data = event?.data?.data;

	if (type === 'request:interaction:done') {
		onInteractionDone(data?.interaction_id);
		queue.remove(data?.interaction_id);
		return true;
	}

	if (
		type !== 'request:user_input' &&
		type !== 'request:elicitation' &&
		!(type === 'confirmation' && data?.tool_call)
	)
		return false;

	if (!callback) return true;
	if (event.chat_id !== currentChatId) {
		callback({ status: 'cancelled', answers: {} });
		return true;
	}
	if (data?.tool_call && isTemporaryChat(event.chat_id) && approvalMode === 'full') {
		callback(true);
		return true;
	}

	queue.enqueue({
		id: data?.interaction_id ?? createId(),
		messageId: event.message_id as string,
		type,
		data,
		callback
	});
	return true;
};
