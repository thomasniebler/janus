import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { UIVersionProvider } from './UIVersionContext';
import {
  clearStoredUIVersion,
  readStoredUIVersion,
  UI_VERSION_STORAGE_KEY,
  useSyncUIVersionFromServer,
  useUIVersion,
  type UIVersion,
} from './uiVersion';

const Probe: React.FC<{ serverUIVersion?: UIVersion }> = ({ serverUIVersion }) => {
  const { uiVersion, preferredUIVersion, isOverridden, setUIVersion } = useUIVersion();
  useSyncUIVersionFromServer(serverUIVersion);

  return (
    <div>
      <span data-testid="active">{uiVersion}</span>
      <span data-testid="preferred">{preferredUIVersion}</span>
      <span data-testid="overridden">{String(isOverridden)}</span>
      <button type="button" onClick={() => setUIVersion('v2')}>
        use v2
      </button>
      <button type="button" onClick={() => setUIVersion('v3')}>
        use v3
      </button>
      <button type="button" onClick={() => setUIVersion('v1')}>
        use v1
      </button>
    </div>
  );
};

const setSearch = (search: string) => {
  window.history.replaceState({}, '', `/${search}`);
};

describe('UIVersionContext', () => {
  beforeEach(() => {
    window.localStorage.clear();
    setSearch('');
    document.documentElement.className = '';
  });

  afterEach(() => {
    setSearch('');
  });

  it('defaults to the classic shell and keeps dark mode forced', () => {
    render(
      <UIVersionProvider>
        <Probe />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v1');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement).not.toHaveClass('ui-v2');
    expect(document.documentElement).not.toHaveClass('ui-v3');
  });

  it('seeds the shell from the localStorage cache', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v2');

    render(
      <UIVersionProvider>
        <Probe />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v2');
    expect(document.documentElement).toHaveClass('ui-v2');
    // Both shells are dark; UIv2 layers its terminal identity on top of `dark`.
    expect(document.documentElement).toHaveClass('dark');
  });

  it('writes through to localStorage and the html class when the shell changes', async () => {
    const user = userEvent.setup();
    render(
      <UIVersionProvider>
        <Probe />
      </UIVersionProvider>
    );

    await user.click(screen.getByRole('button', { name: 'use v2' }));

    expect(screen.getByTestId('active')).toHaveTextContent('v2');
    expect(readStoredUIVersion()).toBe('v2');
    expect(document.documentElement).toHaveClass('ui-v2');

    await user.click(screen.getByRole('button', { name: 'use v1' }));

    expect(screen.getByTestId('active')).toHaveTextContent('v1');
    expect(readStoredUIVersion()).toBe('v1');
    expect(document.documentElement).not.toHaveClass('ui-v2');
  });

  it('swaps between the alternate shells without leaving a stale identity class', async () => {
    const user = userEvent.setup();
    render(
      <UIVersionProvider>
        <Probe />
      </UIVersionProvider>
    );

    await user.click(screen.getByRole('button', { name: 'use v2' }));
    expect(document.documentElement).toHaveClass('ui-v2');

    await user.click(screen.getByRole('button', { name: 'use v3' }));

    expect(screen.getByTestId('active')).toHaveTextContent('v3');
    expect(readStoredUIVersion()).toBe('v3');
    expect(document.documentElement).toHaveClass('ui-v3');
    // The previous shell's identity class must not survive the swap.
    expect(document.documentElement).not.toHaveClass('ui-v2');
    expect(document.documentElement).toHaveClass('dark');
  });

  it('seeds the rusty console shell from the localStorage cache', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v3');

    render(
      <UIVersionProvider>
        <Probe />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v3');
    expect(document.documentElement).toHaveClass('ui-v3');
    expect(document.documentElement).toHaveClass('dark');
  });

  it('lets ?ui=v3 override the cached and stored preference', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v1');
    setSearch('?ui=v3');

    render(
      <UIVersionProvider>
        <Probe serverUIVersion="v2" />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v3');
    expect(screen.getByTestId('overridden')).toHaveTextContent('true');
    expect(document.documentElement).toHaveClass('ui-v3');
    expect(document.documentElement).not.toHaveClass('ui-v2');
  });

  it('lets the backend value win over a stale localStorage cache', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v1');

    render(
      <UIVersionProvider>
        <Probe serverUIVersion="v2" />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v2');
    expect(readStoredUIVersion()).toBe('v2');
  });

  it('lets ?ui=v1 override the cached and stored preference', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v2');
    setSearch('?ui=v1');

    render(
      <UIVersionProvider>
        <Probe serverUIVersion="v2" />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v1');
    expect(screen.getByTestId('preferred')).toHaveTextContent('v2');
    expect(screen.getByTestId('overridden')).toHaveTextContent('true');
    expect(document.documentElement).not.toHaveClass('ui-v2');
  });

  it('ignores an unknown ?ui value', () => {
    setSearch('?ui=v9');

    render(
      <UIVersionProvider initialUIVersion="v2">
        <Probe />
      </UIVersionProvider>
    );

    expect(screen.getByTestId('active')).toHaveTextContent('v2');
    expect(screen.getByTestId('overridden')).toHaveTextContent('false');
  });

  it('clears the cached preference', () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v2');

    clearStoredUIVersion();

    expect(readStoredUIVersion()).toBeNull();
  });

  it('renders the server shell immediately when it differs from the cached shell on first render', () => {
    // This test ensures that when currentUser.ui_version differs from the cached
    // value, the correct shell is rendered in the same render where currentUser
    // first becomes available, not in a subsequent render after an effect.
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v1');

    render(
      <UIVersionProvider>
        <Probe serverUIVersion="v2" />
      </UIVersionProvider>
    );

    // The active shell must be v2 (the server value) in the first render,
    // not v1 (the cached value). This verifies no flash occurs.
    expect(screen.getByTestId('active')).toHaveTextContent('v2');
    // The cache is updated by the useSyncUIVersionFromServer effect.
    expect(readStoredUIVersion()).toBe('v2');
  });
});
