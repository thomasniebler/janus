import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { statusApi } from '../../api';
import { WhatsNewFlash } from './WhatsNewFlash';

vi.mock('../../api', () => ({ statusApi: { getVersion: vi.fn() } }));

const info = (releases: { version: string; items: string[] }[]) => ({
  data: {
    version: '1.13.0',
    changelog_url: '/docs/changelog',
    release_url: 'https://github.test/releases/tag/v1.13.0',
    releases: releases.map((release) => ({
      version: release.version,
      date: '2026-11-11',
      groups: [{ title: 'Features', items: release.items }],
    })),
  },
});

const renderFlash = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <WhatsNewFlash userId={7} />
      </MemoryRouter>
    </QueryClientProvider>
  );

describe('WhatsNewFlash', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(statusApi.getVersion).mockReset();
  });

  it('shows the changes since the last seen version with a changelog link, then remembers the version', async () => {
    window.localStorage.setItem('janus.lastSeenVersion.7', '1.12.0');
    vi.mocked(statusApi.getVersion).mockResolvedValue(
      info([{ version: '1.13.0', items: ['**chat:** narrate multi-step turns', 'WAL mode'] }]) as never
    );

    renderFlash();

    expect(await screen.findByText('Janus updated to v1.13.0')).toBeInTheDocument();
    expect(screen.getByText('chat: narrate multi-step turns')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Read the full changelog' })).toHaveAttribute(
      'href',
      '/docs/changelog'
    );
    expect(statusApi.getVersion).toHaveBeenCalledWith('1.12.0');
    // Not remembered while the notes are still on screen: a reload would otherwise lose them.
    expect(window.localStorage.getItem('janus.lastSeenVersion.7')).toBe('1.12.0');
  });

  it('shows nothing on a first visit and only records the version', async () => {
    vi.mocked(statusApi.getVersion).mockResolvedValue(info([]) as never);

    renderFlash();

    await waitFor(() => expect(window.localStorage.getItem('janus.lastSeenVersion.7')).toBe('1.13.0'));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(statusApi.getVersion).toHaveBeenCalledWith(null);
  });

  it('caps the highlights and counts the rest', async () => {
    window.localStorage.setItem('janus.lastSeenVersion.7', '1.10.0');
    const items = Array.from({ length: 8 }, (_, index) => `change ${index + 1}`);
    vi.mocked(statusApi.getVersion).mockResolvedValue(info([{ version: '1.13.0', items }]) as never);

    renderFlash();

    expect(await screen.findByText('change 5')).toBeInTheDocument();
    expect(screen.queryByText('change 6')).not.toBeInTheDocument();
    expect(screen.getByText('and 3 more')).toBeInTheDocument();
  });

  it('can be dismissed', async () => {
    window.localStorage.setItem('janus.lastSeenVersion.7', '1.12.0');
    vi.mocked(statusApi.getVersion).mockResolvedValue(info([{ version: '1.13.0', items: ['One thing'] }]) as never);
    const user = userEvent.setup();
    renderFlash();

    await user.click(await screen.findByRole('button', { name: 'Dismiss' }));

    expect(screen.queryByText('Janus updated to v1.13.0')).not.toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem('janus.lastSeenVersion.7')).toBe('1.13.0'));
  });

  it('does not remember an unknown version', async () => {
    window.localStorage.setItem('janus.lastSeenVersion.7', '1.12.0');
    vi.mocked(statusApi.getVersion).mockResolvedValue({ data: { ...info([]).data, version: 'unknown' } } as never);

    renderFlash();

    await waitFor(() => expect(statusApi.getVersion).toHaveBeenCalled());
    expect(window.localStorage.getItem('janus.lastSeenVersion.7')).toBe('1.12.0');
  });

  it('keeps one remembered version per user', async () => {
    window.localStorage.setItem('janus.lastSeenVersion.1', '1.13.0');
    vi.mocked(statusApi.getVersion).mockResolvedValue(
      info([{ version: '1.13.0', items: ['New for user 7'] }]) as never
    );

    renderFlash();

    expect(await screen.findByText('New for user 7')).toBeInTheDocument();
    expect(statusApi.getVersion).toHaveBeenCalledWith(null);
  });
});
