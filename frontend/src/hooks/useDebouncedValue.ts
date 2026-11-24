import { useEffect, useRef, useState } from 'react';

/**
 * Returns `value` once it has stopped changing for `delayMs`.
 *
 * Used to keep network lookups (for example the LLM model list) from firing on
 * every keystroke while a user types an API key or base URL.
 *
 * When `resetKey` changes (for example, the provider a credential belongs to),
 * the debounced value snaps to `value` immediately instead of waiting out the
 * delay with the previous key's context — otherwise a fast switch away from a
 * provider mid-debounce would send its lookup with the wrong provider's value.
 */
export function useDebouncedValue<T>(value: T, delayMs: number, resetKey?: unknown): T {
  const [debounced, setDebounced] = useState(value);
  const previousResetKey = useRef(resetKey);

  useEffect(() => {
    if (previousResetKey.current !== resetKey) {
      previousResetKey.current = resetKey;
      setDebounced(value);
      return;
    }

    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs, resetKey]);

  return debounced;
}
