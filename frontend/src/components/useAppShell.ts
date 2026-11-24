import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import type { UIVersion } from '../contexts/uiVersion';
import { appShellRegistry, type AppShellComponent, type AppShellRegistry } from './appShells';

/**
 * Resolves the shell component for `version`, loading its chunk on demand (JAN-179).
 *
 * The current shell is read from the registry through `useSyncExternalStore`, so a chunk
 * that lands at any moment (including between this render and its effects, e.g. via a
 * prefetch) re-renders the host. While a newly selected shell is still downloading, the
 * previously displayed shell stays on screen instead of flashing a loader; `null` is only
 * returned before any shell has loaded. A failed chunk load is rethrown for the error
 * boundary (a stale chunk after a deploy reloads the page first, see `staleChunkReload`).
 */
export const useAppShell = (
  version: UIVersion,
  registry: AppShellRegistry = appShellRegistry
): AppShellComponent | null => {
  const getSnapshot = useCallback(() => registry.get(version), [registry, version]);
  const Shell = useSyncExternalStore(registry.subscribe, getSnapshot);

  const [displayed, setDisplayed] = useState<{ Shell: AppShellComponent } | null>(() =>
    Shell ? { Shell } : null
  );
  const [loadError, setLoadError] = useState<{ error: unknown } | null>(null);

  if (Shell && displayed?.Shell !== Shell) {
    // Adjusting state during render (not in an effect) avoids an extra stale commit.
    setDisplayed({ Shell });
  }

  useEffect(() => {
    let active = true;
    registry.load(version).catch((error: unknown) => {
      if (active) {
        setLoadError({ error: error ?? new Error('Failed to load app shell') });
      }
    });
    return () => {
      active = false;
    };
  }, [registry, version]);

  if (loadError) {
    throw loadError.error;
  }
  return Shell ?? displayed?.Shell ?? null;
};
