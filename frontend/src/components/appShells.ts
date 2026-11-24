import type React from 'react';

import type { UIVersion } from '../contexts/uiVersion';
import { withStaleChunkReload } from '../lib/staleChunkReload';
import type { User } from '../types';

/** The props every app shell accepts, which is what makes the swap a one-liner. */
export interface AppShellProps {
  children: React.ReactNode;
  currentUser: User;
}

export type AppShellComponent = React.ComponentType<AppShellProps>;

export type AppShellLoaders = Record<UIVersion, () => Promise<{ default: AppShellComponent }>>;

/** A cache of loaded shell chunks that React can subscribe to (`useSyncExternalStore`). */
export interface AppShellRegistry {
  /** The shell component if its chunk has already loaded, so it can render synchronously. */
  get: (version: UIVersion) => AppShellComponent | undefined;
  /** Loads (once) and caches a shell's chunk; listeners are notified when it lands. */
  load: (version: UIVersion) => Promise<AppShellComponent>;
  /** Subscribes to cache changes; returns the unsubscribe function. */
  subscribe: (listener: () => void) => () => void;
}

export const createAppShellRegistry = (loaders: AppShellLoaders): AppShellRegistry => {
  const loaded: Partial<Record<UIVersion, AppShellComponent>> = {};
  const pending: Partial<Record<UIVersion, Promise<AppShellComponent>>> = {};
  const listeners = new Set<() => void>();

  const load = (version: UIVersion): Promise<AppShellComponent> => {
    const cached = loaded[version];
    if (cached) {
      return Promise.resolve(cached);
    }
    const inFlight =
      pending[version] ??
      loaders[version]().then(
        (module) => {
          loaded[version] = module.default;
          listeners.forEach((listener) => listener());
          return module.default;
        },
        (error: unknown) => {
          // Let a later attempt retry (e.g. after a transient network failure).
          delete pending[version];
          throw error;
        }
      );
    pending[version] = inFlight;
    return inFlight;
  };

  return {
    get: (version) => loaded[version],
    load,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
};

/**
 * The interchangeable app shells, keyed by UI version, each behind a dynamic `import()`
 * so the chrome is only downloaded when it is rendered or prefetched (JAN-179). Pages
 * and feature components are shared; only the chrome around them differs. A chunk that
 * a deploy removed reloads the tab into the new build instead of crashing it.
 */
export const appShellRegistry = createAppShellRegistry({
  v1: withStaleChunkReload(() => import('./layout/Layout')),
  v2: withStaleChunkReload(() => import('./layout-v2/LayoutV2')),
  v3: withStaleChunkReload(() => import('./layout-v3/LayoutV3')),
});

export const getLoadedAppShell = appShellRegistry.get;
export const loadAppShell = appShellRegistry.load;
