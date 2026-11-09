type CompositionEventLike = { timeStamp: number };

/** Protects IME confirmation keystrokes from triggering composer shortcuts. */
export const createCompositionGuard = (isSafari: boolean) => {
  let isComposing = false;
  let compositionEndedAt = -2e8;

  const onCompositionStart = () => {
    isComposing = true;
  };

  const onCompositionEnd = (event: CompositionEventLike) => {
    compositionEndedAt = event.timeStamp;
    isComposing = false;
  };

  const shouldIgnore = (event: CompositionEventLike) => {
    if (isComposing) return true;
    // Safari can emit a confirmation key just after compositionend.
    if (isSafari && Math.abs(event.timeStamp - compositionEndedAt) < 500) {
      compositionEndedAt = -2e8;
      return true;
    }
    return false;
  };

  return { onCompositionStart, onCompositionEnd, shouldIgnore };
};
