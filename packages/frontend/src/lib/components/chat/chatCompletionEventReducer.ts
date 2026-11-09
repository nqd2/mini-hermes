export type ChatCompletionMessage = Record<string, any>;
export type ChatCompletionEventData = Record<string, any>;

export type ChatCompletionEventDependencies = {
  getOutputText: (output: any[]) => string;
  hapticFeedback: boolean;
  handleError: (error: any, message: ChatCompletionMessage) => void;
  vibrate: (duration: number) => void;
  dispatchAudio: (message: ChatCompletionMessage) => void;
  captureUsageCheckpoint: (message: ChatCompletionMessage) => void;
  logEmptyResponse: () => void;
};

/** Applies provider completion data to the in-memory message and runs its local UI effects. */
export const applyChatCompletionEvent = (
  data: ChatCompletionEventData,
  message: ChatCompletionMessage,
  shared: boolean,
  dependencies: ChatCompletionEventDependencies,
) => {
  const {
    done,
    choices,
    content,
    output,
    sources,
    selected_model_id,
    error,
    usage,
  } = data;

  if (error) {
    if (shared) message.error = error;
    else dependencies.handleError(error, message);
  }

  if (output) {
    message.output = output;
    message.content = dependencies.getOutputText(output);
    if (
      data.type === 'response.output_text.delta' &&
      !shared &&
      dependencies.hapticFeedback
    ) {
      dependencies.vibrate(5);
    }
    if (!shared) dependencies.dispatchAudio(message);
  }

  if (sources && !message?.sources) {
    message.sources = sources;
  }

  if (choices && !output) {
    if (choices[0]?.message?.content) {
      message.content += choices[0]?.message?.content;
      if (!shared) dependencies.dispatchAudio(message);
    } else {
      const value = choices[0]?.delta?.content ?? '';
      if (message.content == '' && value == '\n') {
        dependencies.logEmptyResponse();
      } else {
        message.content += value;
        if (!shared && dependencies.hapticFeedback) dependencies.vibrate(5);
        if (!shared) dependencies.dispatchAudio(message);
      }
    }
  }

  if (content && !output) {
    message.content = content;
    if (!shared && dependencies.hapticFeedback) dependencies.vibrate(5);
    if (!shared) dependencies.dispatchAudio(message);
  }

  if (selected_model_id) {
    message.selectedModelId = selected_model_id;
    message.arena = true;
  }

  if (usage) {
    message.usage = usage;
    dependencies.captureUsageCheckpoint(message);
  }

  return { done };
};
