export type PromptVariableControllerDependencies = {
  extractInputVariables: (text: string) => Record<string, any>;
  onVariablesDetected: (variables: Record<string, any>) => void;
  onInputModalOpened: () => void;
  onInputValuesResolved: (values: Record<string, any>) => void;
  readClipboardText: () => Promise<string>;
  readClipboardItems: () => Promise<
    { types: readonly string[]; getType: (type: string) => Promise<Blob> }[]
  >;
  onClipboardTextError: (error: unknown) => void;
  onClipboardItemsError: (error: unknown) => void;
  createClipboardImageFile: (blob: Blob, name: string, type: string) => File;
  onClipboardImage: (file: File) => void;
  getUserPosition: () => Promise<unknown>;
  onLocationError: (error: unknown) => void;
  getSessionUser: (
    token: string,
  ) => Promise<Record<string, any> | null | undefined>;
  getToken: () => string;
  getLanguage: () => string | null;
  getAge: (date: string) => string;
  getFormattedDate: () => string;
  getFormattedTime: () => string;
  getCurrentDateTime: () => string;
  getUserTimezone: () => string;
  getWeekday: () => string;
};

export const createPromptVariableController = (
  dependencies: PromptVariableControllerDependencies,
) => {
  let completeCurrentPrompt = (_values: Record<string, any>) => {};

  const resolveInputVariables = async (text: string): Promise<string> => {
    const variables = dependencies.extractInputVariables(text);
    dependencies.onVariablesDetected(variables);
    if (Object.keys(variables).length === 0) return text;

    dependencies.onInputModalOpened();
    return new Promise<string>((resolve) => {
      completeCurrentPrompt = (values) => {
        dependencies.onInputValuesResolved(values);
        resolve(text);
      };
    });
  };

  const replaceTextVariables = async (initialText: string): Promise<string> => {
    let text = initialText;
    if (text.includes('{{CLIPBOARD}}')) {
      const clipboardText = await dependencies
        .readClipboardText()
        .catch((error) => {
          dependencies.onClipboardTextError(error);
          return '{{CLIPBOARD}}';
        });

      const clipboardItems = await dependencies
        .readClipboardItems()
        .catch((error) => {
          dependencies.onClipboardItemsError(error);
          return [];
        });

      for (const item of clipboardItems) {
        for (const type of item.types) {
          if (type.startsWith('image/')) {
            const blob = await item.getType(type);
            const file = dependencies.createClipboardImageFile(
              blob,
              `clipboard-image.${type.split('/')[1]}`,
              type,
            );
            dependencies.onClipboardImage(file);
          }
        }
      }

      text = text.replaceAll('{{CLIPBOARD}}', () =>
        clipboardText.replaceAll('\r\n', '\n'),
      );
    }

    if (text.includes('{{USER_LOCATION}}')) {
      let location: unknown;
      try {
        location = await dependencies.getUserPosition();
      } catch (error) {
        dependencies.onLocationError(error);
        location = 'LOCATION_UNKNOWN';
      }
      text = text.replaceAll('{{USER_LOCATION}}', String(location));
    }

    const sessionUser = await dependencies.getSessionUser(
      dependencies.getToken(),
    );
    if (text.includes('{{USER_NAME}}')) {
      text = text.replaceAll('{{USER_NAME}}', sessionUser?.name || 'User');
    }
    if (text.includes('{{USER_EMAIL}}') && sessionUser?.email) {
      text = text.replaceAll('{{USER_EMAIL}}', sessionUser.email);
    }
    if (text.includes('{{USER_ROLE}}') && sessionUser?.role) {
      text = text.replaceAll('{{USER_ROLE}}', sessionUser.role);
    }
    if (text.includes('{{USER_BIO}}') && sessionUser?.bio) {
      text = text.replaceAll('{{USER_BIO}}', sessionUser.bio);
    }
    if (text.includes('{{USER_GENDER}}') && sessionUser?.gender) {
      text = text.replaceAll('{{USER_GENDER}}', sessionUser.gender);
    }
    if (text.includes('{{USER_BIRTH_DATE}}') && sessionUser?.date_of_birth) {
      text = text.replaceAll('{{USER_BIRTH_DATE}}', sessionUser.date_of_birth);
    }
    if (text.includes('{{USER_AGE}}') && sessionUser?.date_of_birth) {
      text = text.replaceAll(
        '{{USER_AGE}}',
        dependencies.getAge(sessionUser.date_of_birth),
      );
    }
    if (text.includes('{{USER_LANGUAGE}}')) {
      text = text.replaceAll(
        '{{USER_LANGUAGE}}',
        dependencies.getLanguage() || 'en-US',
      );
    }
    if (text.includes('{{CURRENT_DATE}}')) {
      text = text.replaceAll(
        '{{CURRENT_DATE}}',
        dependencies.getFormattedDate(),
      );
    }
    if (text.includes('{{CURRENT_TIME}}')) {
      text = text.replaceAll(
        '{{CURRENT_TIME}}',
        dependencies.getFormattedTime(),
      );
    }
    if (text.includes('{{CURRENT_DATETIME}}')) {
      text = text.replaceAll(
        '{{CURRENT_DATETIME}}',
        dependencies.getCurrentDateTime(),
      );
    }
    if (text.includes('{{CURRENT_TIMEZONE}}')) {
      text = text.replaceAll(
        '{{CURRENT_TIMEZONE}}',
        dependencies.getUserTimezone(),
      );
    }
    if (text.includes('{{CURRENT_WEEKDAY}}')) {
      text = text.replaceAll('{{CURRENT_WEEKDAY}}', dependencies.getWeekday());
    }
    return text;
  };

  return {
    resolveInputVariables,
    completeInputVariables(values: Record<string, any>) {
      completeCurrentPrompt(values);
    },
    replaceTextVariables,
  };
};
