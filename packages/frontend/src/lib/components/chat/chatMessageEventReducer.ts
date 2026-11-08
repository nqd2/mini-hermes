type ChatMessage = Record<string, any>;

type ChatMessageEventResult = {
	handled: boolean;
	message: ChatMessage | null;
	tasks?: any[];
	tasksUpdated?: boolean;
	scrollToEmbeds?: boolean;
};

/** Applies simple message-shaped socket events; lifecycle events stay with Chat. */
export const reduceChatMessageEvent = (
	message: ChatMessage | null,
	type: string,
	data: Record<string, any> | null = {},
	options: { temporaryChat?: boolean } = {}
): ChatMessageEventResult => {
	if (!message) return { handled: false, message };
	const payload = data ?? {};

	switch (type) {
		case 'status':
			return {
				handled: true,
				message: {
					...message,
					statusHistory: [...(message.statusHistory ?? []), data]
				}
			};
		case 'chat:message:delta':
		case 'message':
			return {
				handled: true,
				message: { ...message, content: message.content + payload.content }
			};
		case 'chat:message':
		case 'replace':
			return { handled: true, message: { ...message, content: payload.content } };
		case 'chat:message:voice':
			return {
				handled: true,
				message: { ...message, meta: { ...message.meta, voice: payload.voice } }
			};
		case 'chat:message:files':
		case 'files':
			return {
				handled: true,
				message: {
					...message,
					files: options.temporaryChat
						? [...(message.files ?? []), ...payload.files]
						: payload.files
				}
			};
		case 'chat:message:tasks':
			return { handled: true, message, tasks: payload.tasks, tasksUpdated: true };
		case 'chat:message:embeds':
		case 'embeds':
			return {
				handled: true,
				message: { ...message, embeds: payload.embeds },
				scrollToEmbeds: true
			};
		default:
			return { handled: false, message };
	}
};
