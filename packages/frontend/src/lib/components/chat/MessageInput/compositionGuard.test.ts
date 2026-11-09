import { describe, expect, it } from 'vitest';
import { createCompositionGuard } from './compositionGuard';

describe('createCompositionGuard', () => {
  it('blocks shortcuts during composition for every browser', () => {
    const guard = createCompositionGuard(false);
    guard.onCompositionStart();

    expect(guard.shouldIgnore({ timeStamp: 1000 })).toBe(true);
    guard.onCompositionEnd({ timeStamp: 1100 });
    expect(guard.shouldIgnore({ timeStamp: 1200 })).toBe(false);
  });

  it('ignores one Safari key event shortly after composition ends', () => {
    const guard = createCompositionGuard(true);
    guard.onCompositionStart();
    guard.onCompositionEnd({ timeStamp: 1000 });

    expect(guard.shouldIgnore({ timeStamp: 1499 })).toBe(true);
    expect(guard.shouldIgnore({ timeStamp: 1500 })).toBe(false);
  });

  it('does not apply the Safari recovery window in other browsers', () => {
    const guard = createCompositionGuard(false);
    guard.onCompositionEnd({ timeStamp: 1000 });

    expect(guard.shouldIgnore({ timeStamp: 1001 })).toBe(false);
  });
});
