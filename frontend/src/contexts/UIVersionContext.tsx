import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  DEFAULT_UI_VERSION,
  readForcedUIVersion,
  readStoredUIVersion,
  UI_VERSION_HTML_CLASSES,
  UIVersionContext,
  writeStoredUIVersion,
  type UIVersion,
  type UIVersionContextValue,
} from './uiVersion';

/** Every identity class any shell can own, so switching can clear the others. */
const ALL_SHELL_CLASSES = Object.values(UI_VERSION_HTML_CLASSES).filter(
  (className): className is string => className !== null
);

interface UIVersionProviderProps {
  children: React.ReactNode;
  /** Test seam: initial preference when localStorage should not be consulted. */
  initialUIVersion?: UIVersion;
}

/**
 * Owns which app shell is rendered and keeps the `<html>` classes in sync.
 *
 * Precedence: a `?ui=` query override wins for the current page load, otherwise the
 * localStorage cache seeds the value and the backend (`/api/auth/me`) reconciles it.
 */
export const UIVersionProvider: React.FC<UIVersionProviderProps> = ({ children, initialUIVersion }) => {
  const forcedUIVersion = useMemo(() => readForcedUIVersion(), []);
  const [preferredUIVersion, setPreferredUIVersion] = useState<UIVersion>(
    () => initialUIVersion ?? readStoredUIVersion() ?? DEFAULT_UI_VERSION
  );

  const uiVersion = forcedUIVersion ?? preferredUIVersion;

  useEffect(() => {
    // Every shell is dark: the shared feature components always render their dark
    // palette, and a shell's identity class (if it has one) layers on top of it.
    const { classList } = document.documentElement;
    classList.add('dark');

    const activeClass = UI_VERSION_HTML_CLASSES[uiVersion];
    for (const shellClass of ALL_SHELL_CLASSES) {
      classList.toggle(shellClass, shellClass === activeClass);
    }
  }, [uiVersion]);

  const setUIVersion = useCallback((version: UIVersion) => {
    setPreferredUIVersion(version);
    writeStoredUIVersion(version);
  }, []);

  const value = useMemo<UIVersionContextValue>(
    () => ({ uiVersion, preferredUIVersion, isOverridden: forcedUIVersion !== null, setUIVersion }),
    [uiVersion, preferredUIVersion, forcedUIVersion, setUIVersion]
  );

  return <UIVersionContext.Provider value={value}>{children}</UIVersionContext.Provider>;
};
