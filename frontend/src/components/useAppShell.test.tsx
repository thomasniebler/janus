import React, { useLayoutEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { UIVersion } from '../contexts/uiVersion';
import { createAppShellRegistry, type AppShellComponent, type AppShellLoaders } from './appShells';
import { useAppShell } from './useAppShell';

const makeShell = (name: string): AppShellComponent => {
  const Shell: AppShellComponent = ({ children }) => (
    <div data-testid="shell" data-shell={name}>
      {children}
    </div>
  );
  return Shell;
};

/** Loaders whose chunks only "arrive" when the test resolves them. */
const deferredLoaders = () => {
  const resolvers: Partial<Record<UIVersion, () => void>> = {};
  const loaders = Object.fromEntries(
    (['v1', 'v2', 'v3'] as const).map((version) => [
      version,
      () =>
        new Promise<{ default: AppShellComponent }>((resolve) => {
          resolvers[version] = () => resolve({ default: makeShell(version) });
        }),
    ])
  ) as AppShellLoaders;
  return { loaders, resolvers };
};

const tick = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

describe('useAppShell (JAN-179)', () => {
  let container: HTMLDivElement;
  let previousActEnvironment: unknown;
  const globalWithAct = globalThis as { IS_REACT_ACT_ENVIRONMENT?: unknown };

  beforeEach(() => {
    // Real scheduling (no act()) so passive effects run in a later task than the commit,
    // which is the window the race lives in.
    previousActEnvironment = globalWithAct.IS_REACT_ACT_ENVIRONMENT;
    globalWithAct.IS_REACT_ACT_ENVIRONMENT = false;
    container = document.createElement('div');
    document.body.appendChild(container);
  });

  afterEach(() => {
    globalWithAct.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    container.remove();
  });

  it('renders a shell whose chunk lands between the render and the effect', async () => {
    const { loaders, resolvers } = deferredLoaders();
    const registry = createAppShellRegistry(loaders);
    // A prefetch (as AppRouter does) is already in flight when the host first renders.
    void registry.load('v2');

    const Host: React.FC = () => {
      const Shell = useAppShell('v2', registry);
      return Shell ? <Shell currentUser={{ id: 1, username: 'u', is_admin: false }}>page</Shell> : <p>loading</p>;
    };
    // Lands the chunk during the commit: the cache fills in the microtasks after the layout
    // effect, i.e. after the host rendered an empty cache but before its passive effect ran.
    const LandChunkDuringCommit: React.FC = () => {
      useLayoutEffect(() => resolvers.v2?.(), []);
      return null;
    };

    const root = createRoot(container);
    root.render(
      <>
        <Host />
        <LandChunkDuringCommit />
      </>
    );
    await tick();

    expect(registry.get('v2')).toBeDefined();
    expect(container.querySelector('[data-testid="shell"]')?.getAttribute('data-shell')).toBe('v2');
    root.unmount();
  });

  it('keeps the previous shell on screen until the newly selected one has loaded', async () => {
    const { loaders, resolvers } = deferredLoaders();
    const registry = createAppShellRegistry(loaders);
    const v1 = registry.load('v1');
    resolvers.v1?.();
    await v1;

    const Host: React.FC<{ version: UIVersion }> = ({ version }) => {
      const Shell = useAppShell(version, registry);
      return Shell ? <Shell currentUser={{ id: 1, username: 'u', is_admin: false }}>page</Shell> : <p>loading</p>;
    };
    const shellName = () => container.querySelector('[data-testid="shell"]')?.getAttribute('data-shell');

    const root = createRoot(container);
    root.render(<Host version="v1" />);
    await tick();
    expect(shellName()).toBe('v1');

    root.render(<Host version="v3" />);
    await tick();
    expect(shellName()).toBe('v1');
    expect(container.textContent).not.toContain('loading');

    resolvers.v3?.();
    await tick();
    expect(shellName()).toBe('v3');
    root.unmount();
  });
});
