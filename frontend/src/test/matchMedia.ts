import { vi } from 'vitest';

/**
 * jsdom has no `window.matchMedia`; this installs one whose every query reports
 * `matches`. Shells use it (through `useMediaQuery`) to pick the chat placement,
 * so `true` means "desktop" (`lg`+) and `false` means "narrow viewport".
 * Undo with `vi.unstubAllGlobals()`.
 */
export const stubMatchMedia = (matches: boolean) => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
};
