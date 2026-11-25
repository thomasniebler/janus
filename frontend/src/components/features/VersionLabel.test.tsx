import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { statusApi } from '../../api';
import { VersionLabel } from './VersionLabel';

// The shared factory (see src/test/apiMock.ts): with `isolate: false`, a partial factory here lost to
// Settings.test's registration whenever both ran in one worker, so VersionLabel called an unconfigured mock.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

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
