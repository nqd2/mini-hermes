import { describe, expect, it, vi } from 'vitest';
import {
  estimateContextUsage,
  estimateMessagesTokens,
  estimateTokens,
} from './contextUsage';

describe('estimateContextUsage', () => {
  it('uses the latest summary as the beginning of the active context', () => {
    const messages = [
      { content: 'old turn', context_summary: 'older summary' },
      { content: 'turn after old summary' },
      { content: 'active turn', contextSummary: 'latest summary' },
      { content: 'newest turn' },
    ];
    const getUsageTokenCount = vi.fn(() => 0);

    const result = estimateContextUsage(
      messages,
      'system',
      null,
      getUsageTokenCount,
    );

    const activeMessages = messages.slice(2);
    const expectedTokens =
      estimateTokens('system') +
      estimateTokens('latest summary') +
      estimateMessagesTokens(activeMessages);
    expect(result).toEqual({
      tokens: expectedTokens,
      estimated_tokens: expectedTokens,
      threshold: null,
      percent: null,
      source: 'estimated',
    });
    expect(getUsageTokenCount).toHaveBeenCalledTimes(activeMessages.length);
  });

  it('continues from the latest usage checkpoint and applies the configured threshold', () => {
    const messages = [
      { content: 'covered prompt', usage: { prompt_tokens: 1 } },
      { content: 'new assistant response' },
      { content: 'new user request' },
    ];
    const getUsageTokenCount = vi.fn(
      (usage?: Record<string, unknown> | null) => (usage ? 80 : 0),
    );

    const result = estimateContextUsage(
      messages,
      'system',
      200,
      getUsageTokenCount,
    );

    const expectedTokens = 80 + estimateMessagesTokens(messages.slice(1));
    expect(result).toEqual({
      tokens: expectedTokens,
      estimated_tokens: expectedTokens,
      threshold: 200,
      percent: Math.round((expectedTokens / 200) * 100),
      source: 'estimated',
    });
    expect(getUsageTokenCount).toHaveBeenCalledWith(messages[0].usage);
  });
});
