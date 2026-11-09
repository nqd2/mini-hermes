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
