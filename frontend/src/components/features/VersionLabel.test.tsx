import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { statusApi } from '../../api';
import { VersionLabel } from './VersionLabel';

vi.mock('../../api', () => ({ statusApi: { getVersion: vi.fn() } }));

describe('VersionLabel', () => {
  it('links the running version to the changelog', async () => {
    vi.mocked(statusApi.getVersion).mockResolvedValue({
      data: { version: '1.13.0', changelog_url: '/docs/changelog', release_url: 'x', releases: [] },
    } as never);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <VersionLabel />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const link = await screen.findByRole('link', { name: 'Janus v1.13.0 · Changelog' });
    expect(link).toHaveAttribute('href', '/docs/changelog');
  });
});
