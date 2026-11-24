import { useCallback, useSyncExternalStore } from 'react';

/** Tailwind's `lg` breakpoint: the width at which the shells dock chat in a sidebar. */
export const DESKTOP_CHAT_MEDIA_QUERY = '(min-width: 1024px)';

const getMediaQueryList = (query: string): MediaQueryList | null =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null;

/**
 * Whether `query` currently matches, kept in sync with viewport changes.
 *
 * Lets a shell render one chat view in the right place instead of mounting a
 * CSS-hidden copy for every breakpoint (JAN-248). Returns `false` where
 * `matchMedia` is unavailable (for example jsdom without a stub).
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mediaQueryList = getMediaQueryList(query);
      if (!mediaQueryList) return () => {};
      mediaQueryList.addEventListener('change', onChange);
      return () => mediaQueryList.removeEventListener('change', onChange);
    },
    [query]
  );
  const getSnapshot = () => getMediaQueryList(query)?.matches ?? false;

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
