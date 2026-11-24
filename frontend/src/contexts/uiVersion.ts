import { createContext, useContext, useEffect } from 'react';

/** Every selectable app shell, in the order they are offered to the user. */
export const UI_VERSIONS = ['v1', 'v2', 'v3'] as const;

export type UIVersion = (typeof UI_VERSIONS)[number];

/** The shell every fallback path lands on. Also what the crash kill switch restores. */
export const DEFAULT_UI_VERSION: UIVersion = 'v1';

/**
 * `<html>` identity class per shell. UIv1 is the baseline and adds nothing; every
 * other shell layers its identity on top of the always-present `dark` class.
 */
export const UI_VERSION_HTML_CLASSES: Record<UIVersion, string | null> = {
  v1: null,
  v2: 'ui-v2',
  v3: 'ui-v3',
};

/** localStorage key mirrored by the anti-flash bootstrap `public/ui-shell-bootstrap.js`. */
export const UI_VERSION_STORAGE_KEY = 'ui_version';

/** Query parameter that force-selects a shell for the current page load. */
export const UI_VERSION_QUERY_PARAM = 'ui';

const isUIVersion = (value: unknown): value is UIVersion =>
  UI_VERSIONS.includes(value as UIVersion);

/** Reads the cached shell preference, tolerating browsers with storage disabled. */
export const readStoredUIVersion = (): UIVersion | null => {
  try {
    const stored = window.localStorage.getItem(UI_VERSION_STORAGE_KEY);
    return isUIVersion(stored) ? stored : null;
  } catch {
    return null;
  }
};

/** Write-through cache update so the next page load renders the right shell immediately. */
export const writeStoredUIVersion = (version: UIVersion): void => {
  try {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, version);
  } catch {
    // Storage is unavailable (private mode, blocked cookies) — the backend stays authoritative.
  }
};

/** Drops the cached shell preference. Used by the crash-recovery kill switch. */
export const clearStoredUIVersion = (): void => {
  try {
    window.localStorage.removeItem(UI_VERSION_STORAGE_KEY);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
};

/** Reads a `?ui=v1` / `?ui=v2` / `?ui=v3` override, which wins over storage and the backend. */
export const readForcedUIVersion = (search?: string): UIVersion | null => {
  try {
    const query = search ?? window.location.search;
    const forced = new URLSearchParams(query).get(UI_VERSION_QUERY_PARAM);
    return isUIVersion(forced) ? forced : null;
  } catch {
    return null;
  }
};

export interface UIVersionContextValue {
  /** The shell actually rendered right now. */
  uiVersion: UIVersion;
  /** The stored preference, which may differ from `uiVersion` while an override is active. */
  preferredUIVersion: UIVersion;
  /** True when a `?ui=` query parameter is pinning the shell for this page load. */
  isOverridden: boolean;
  /** Updates the preference locally (state + localStorage). Server persistence is the caller's job. */
  setUIVersion: (version: UIVersion) => void;
}

export const UIVersionContext = createContext<UIVersionContextValue | null>(null);

export const useUIVersion = (): UIVersionContextValue => {
  const context = useContext(UIVersionContext);
  if (!context) {
    throw new Error('useUIVersion must be used inside a UIVersionProvider');
  }
  return context;
};

/**
 * Reconciles the locally cached preference with the authoritative backend value.
 * The backend wins: a mismatch silently updates both React state and localStorage.
 */
export const useSyncUIVersionFromServer = (serverUIVersion: UIVersion | undefined): void => {
  const { preferredUIVersion, setUIVersion } = useUIVersion();

  useEffect(() => {
    if (serverUIVersion && serverUIVersion !== preferredUIVersion) {
      setUIVersion(serverUIVersion);
    }
    // `preferredUIVersion` is intentionally omitted: a local (optimistic) change must not be
    // reverted before the PATCH lands and refreshes the cached `me` payload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverUIVersion, setUIVersion]);
};
