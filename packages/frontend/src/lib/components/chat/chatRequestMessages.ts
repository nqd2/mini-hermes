export type ChatRequestMessageFile = {
  type?: string;
  content_type?: string;
  url?: string;
  [key: string]: any;
};

export type ChatRequestHistoryMessage = {
  role: string;
  content?: any;
  merged?: { content?: any };
  contextSummary?: any;
  files?: ChatRequestMessageFile[];
  output?: any[];
  model?: string;
  meta?: any;
  [key: string]: any;
};

export type ChatRequestMessagesDependencies = {
  systemPrompt?: unknown;
  isTemporaryChat: boolean;
  historyMessages: ChatRequestHistoryMessage[];
  processDetails: (content: any) => any;
  isRasterImageContentType: (contentType?: string) => boolean;
};

/** Builds the model message list while preserving temporary-chat multimodal behavior. */
export const buildChatRequestMessages = ({
  systemPrompt,
  isTemporaryChat,
  historyMessages,
  processDetails,
  isRasterImageContentType,
}: ChatRequestMessagesDependencies) => {
  let messages: any[] = [
    systemPrompt
      ? {
          role: 'system',
          content: `${systemPrompt}`,
        }
      : undefined,
  ].filter(Boolean);

  if (!isTemporaryChat) return messages;

  messages = [
    ...messages,
    ...historyMessages.map((message) => ({
      ...message,
      ...(message.output && message.role === 'assistant'
        ? { output: message.output }
        : { content: processDetails(message.content) }),
    })),
  ].filter((message) => message);

  messages = messages
    .map((message) => {
      const imageFiles = (message?.files ?? []).filter(
        (file) =>
          file.type === 'image' || isRasterImageContentType(file?.content_type),
      );

      if (message.output && message.role === 'assistant') {
        return {
          role: message.role,
          model: message.model,
          output: message.output,
          meta: message.meta,
        };
      }

      if (message.role === 'user' && imageFiles.length > 0) {
        return {
          id: message.id,
          role: message.role,
          contextSummary: message?.contextSummary,
          content: [
            {
              type: 'text',
              text: message?.merged?.content ?? message.content,
            },
            ...imageFiles.map((file) => ({
              type: 'image_url',
              image_url: {
                url: file.url,
              },
            })),
          ],
        };
      }

      return {
        id: message.id,
        role: message.role,
        contextSummary: message?.contextSummary,
        content: message?.merged?.content ?? message.content,
        model: message.model,
        meta: message.meta,
      };
    })
    .filter(
      (message) =>
        message?.role === 'user' ||
        message?.content?.trim() ||
        message?.output?.length,
    );

  return messages;
};
