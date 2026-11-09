import { describe, expect, it, vi } from 'vitest';
import { createPromptVariableController } from './promptVariableController';

const makeDependencies = (overrides: Record<string, any> = {}) => ({
  extractInputVariables: vi.fn(() => ({})),
  onVariablesDetected: vi.fn(),
  onInputModalOpened: vi.fn(),
  onInputValuesResolved: vi.fn(),
  readClipboardText: vi.fn(async () => 'first\r\nsecond'),
  readClipboardItems: vi.fn(async () => []),
  onClipboardTextError: vi.fn(),
  onClipboardItemsError: vi.fn(),
  createClipboardImageFile: vi.fn(
    (blob: Blob, name: string, type: string) =>
      ({ blob, name, type }) as unknown as File,
  ),
  onClipboardImage: vi.fn(),
  getUserPosition: vi.fn(async () => '51, -1'),
  onLocationError: vi.fn(),
  getSessionUser: vi.fn(async () => ({
    name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    bio: 'Engineer',
    gender: 'unspecified',
    date_of_birth: '1990-01-02',
  })),
  getToken: vi.fn(() => 'token'),
  getLanguage: vi.fn(() => 'vi-VN'),
  getAge: vi.fn(() => '36'),
  getFormattedDate: vi.fn(() => 'today'),
  getFormattedTime: vi.fn(() => 'now'),
  getCurrentDateTime: vi.fn(() => 'today now'),
  getUserTimezone: vi.fn(() => 'Asia/Bangkok'),
  getWeekday: vi.fn(() => 'Monday'),
  ...overrides,
});

describe('createPromptVariableController', () => {
  it('returns unchanged text immediately when there are no input variables', async () => {
    const dependencies = makeDependencies();
    const controller = createPromptVariableController(dependencies);

    await expect(controller.resolveInputVariables('hello')).resolves.toBe(
      'hello',
    );
    expect(dependencies.extractInputVariables).toHaveBeenCalledWith('hello');
    expect(dependencies.onVariablesDetected).toHaveBeenCalledWith({});
    expect(dependencies.onInputModalOpened).not.toHaveBeenCalled();
  });

  it('waits for modal values, applies them, then resolves with the original text', async () => {
    const dependencies = makeDependencies({
      extractInputVariables: vi.fn(() => ({ CUSTOMER: 'Name', DATE: 'Date' })),
    });
    const controller = createPromptVariableController(dependencies);
    let settled = false;
    const result = controller.resolveInputVariables('Hello {{CUSTOMER}}');
    void result.then(() => {
      settled = true;
    });
    await Promise.resolve();

    expect(settled).toBe(false);
    expect(dependencies.onVariablesDetected).toHaveBeenCalledWith({
      CUSTOMER: 'Name',
      DATE: 'Date',
    });
    expect(dependencies.onInputModalOpened).toHaveBeenCalledOnce();

    controller.completeInputVariables({ CUSTOMER: 'Ada' });
    await expect(result).resolves.toBe('Hello {{CUSTOMER}}');
    expect(dependencies.onInputValuesResolved).toHaveBeenCalledWith({
      CUSTOMER: 'Ada',
    });
  });

  it('substitutes clipboard text and forwards pasted clipboard images', async () => {
    const blob = new Blob(['png']);
    const dependencies = makeDependencies({
      readClipboardItems: vi.fn(async () => [
        {
          types: ['text/plain', 'image/png'],
          getType: vi.fn(async () => blob),
        },
      ]),
    });
    const controller = createPromptVariableController(dependencies);

    await expect(
      controller.replaceTextVariables('Copied: {{CLIPBOARD}}'),
    ).resolves.toBe('Copied: first\nsecond');
    expect(dependencies.getSessionUser).toHaveBeenCalledWith('token');
    expect(dependencies.createClipboardImageFile).toHaveBeenCalledWith(
      blob,
      'clipboard-image.png',
      'image/png',
    );
    expect(dependencies.onClipboardImage).toHaveBeenCalledWith({
      blob,
      name: 'clipboard-image.png',
      type: 'image/png',
    });
  });

  it('substitutes location, user profile, locale and date tokens', async () => {
    const dependencies = makeDependencies();
    const controller = createPromptVariableController(dependencies);

    await expect(
      controller.replaceTextVariables(
        '{{USER_LOCATION}} {{USER_NAME}} {{USER_EMAIL}} {{USER_ROLE}} {{USER_BIO}} {{USER_GENDER}} {{USER_BIRTH_DATE}} {{USER_AGE}} {{USER_LANGUAGE}} {{CURRENT_DATE}} {{CURRENT_TIME}} {{CURRENT_DATETIME}} {{CURRENT_TIMEZONE}} {{CURRENT_WEEKDAY}}',
      ),
    ).resolves.toBe(
      '51, -1 Ada ada@example.com admin Engineer unspecified 1990-01-02 36 vi-VN today now today now Asia/Bangkok Monday',
    );
  });

  it('keeps clipboard placeholder on failure and substitutes unknown location on denial', async () => {
    const clipboardError = new Error('clipboard denied');
    const locationError = new Error('location denied');
    const dependencies = makeDependencies({
      readClipboardText: vi.fn(async () => {
        throw clipboardError;
      }),
      readClipboardItems: vi.fn(async () => {
        throw clipboardError;
      }),
      getUserPosition: vi.fn(async () => {
        throw locationError;
      }),
    });
    const controller = createPromptVariableController(dependencies);

    await expect(
      controller.replaceTextVariables('{{CLIPBOARD}} {{USER_LOCATION}}'),
    ).resolves.toBe('{{CLIPBOARD}} LOCATION_UNKNOWN');
    expect(dependencies.onClipboardTextError).toHaveBeenCalledWith(
      clipboardError,
    );
    expect(dependencies.onClipboardItemsError).toHaveBeenCalledWith(
      clipboardError,
    );
    expect(dependencies.onLocationError).toHaveBeenCalledWith(locationError);
  });
});
