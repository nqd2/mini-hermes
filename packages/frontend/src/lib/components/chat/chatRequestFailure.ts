export type ChatRequestFailureDependencies = {
  error: any;
  responseMessageId: string;
  responseMessage: Record<string, any>;
  history: {
    currentId: string | null;
    messages: Record<string, any>;
  };
  translate: (key: string) => string;
  notify: (message: string) => void;
};

export type OpenAIResponseErrorDependencies = {
  error: any;
  responseMessage: Record<string, any>;
  history: { messages: Record<string, any> };
  getErrorMessage: (error: any, fallback: string) => string;
  fallbackMessage: string;
  notify: (message: string) => void;
};

/** Records an OpenAI-compatible response error and avoids duplicate notifications. */
export const applyOpenAIResponseError = ({
  error,
  responseMessage,
  history,
  getErrorMessage,
  fallbackMessage,
  notify,
}: OpenAIResponseErrorDependencies): void => {
  const responseFailed = responseMessage.done && responseMessage.error;
  const errorMessage = getErrorMessage(error, fallbackMessage);
  responseMessage.error = { content: errorMessage };
  responseMessage.done = true;
  if (responseMessage.statusHistory) {
    responseMessage.statusHistory = responseMessage.statusHistory.filter(
      (status: Record<string, any>) => status.action !== 'knowledge_search'
    );
  }
  history.messages[responseMessage.id] = responseMessage;
  if (!responseFailed) notify(errorMessage);
};

/** Normalizes request errors for display and completes the active response in history. */
export const applyChatRequestFailure = ({
  error,
  responseMessageId,
  responseMessage,
  history,
  translate,
  notify,
}: ChatRequestFailureDependencies): null => {
  let errorMessage = error;
  if (error?.error?.message) {
    errorMessage = error.error.message;
  } else if (error?.message) {
    errorMessage = error.message;
  }

  if (typeof errorMessage === 'object') {
    errorMessage = translate('Uh-oh! There was an issue with the response.');
  }

  notify(`${errorMessage}`);
  responseMessage.error = { content: error };
  responseMessage.done = true;
  history.messages[responseMessageId] = responseMessage;
  history.currentId = responseMessageId;

  return null;
};
