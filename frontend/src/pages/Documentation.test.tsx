import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { docsApi } from '../api';
import Documentation from './Documentation';
import { normalizeDocHref } from './documentationRoutes';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

const nav = [
  {
    name: 'Getting Started',
    children: [{ name: 'Getting Started', path: 'index.md' }],
  },
  {
    name: 'Technical Spec',
    children: [{ name: 'Overview', path: 'tech-spec.md' }],
  },
];

const pageContent: Record<string, string> = {
  'index.md': '[Quickstart](quickstart.md)',
  'quickstart.md': '# Quickstart',
  'tech-spec.md': '[Technical Spec Index](docs/docs/tech-spec/)',
};

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}</div>;
};

const renderDocumentation = (initialEntry: string) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <Routes>
          <Route path="/docs/*" element={<Documentation />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Documentation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(docsApi.getNav).mockResolvedValue({ data: nav });
    vi.mocked(docsApi.getPage).mockImplementation(async (path: string) => ({
      data: pageContent[path] ?? '# Missing fixture',
    }));
  });

  it('normalizes repo-relative trailing-slash links to the clean docs route', () => {
    expect(normalizeDocHref('tech-spec', 'docs/docs/tech-spec/')).toBe('/docs/tech-spec');
  });

  it('normalizes relative markdown links to clean docs routes', () => {
    expect(normalizeDocHref('index', 'quickstart.md')).toBe('/docs/quickstart');
    expect(normalizeDocHref('tech-spec/index', 'backend-api.md')).toBe('/docs/tech-spec/backend-api');
  });

  it('navigates internal documentation links through React Router', async () => {
    renderDocumentation('/docs/index');
    const user = userEvent.setup();

    const quickstartLink = await screen.findByRole('link', { name: 'Quickstart' });
    expect(quickstartLink).toHaveAttribute('href', '/docs/quickstart');

    await user.click(quickstartLink);

    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('/docs/quickstart'));
    await waitFor(() => expect(docsApi.getPage).toHaveBeenCalledWith('quickstart.md'));
  });

  // Found by the JAN-225 e2e smoke test: a link to a missing page rendered an empty article.
  it('says a page does not exist instead of rendering an empty page', async () => {
    vi.mocked(docsApi.getPage).mockRejectedValue(
      new AxiosError('Not Found', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 404,
        statusText: 'Not Found',
        data: { detail: 'Documentation page not found' },
        headers: {},
        config: { headers: new AxiosHeaders() },
      }),
    );
    renderDocumentation('/docs/no-such-page');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Page not found');
    expect(alert).toHaveTextContent('no-such-page');
    expect(screen.getByRole('link', { name: 'Go to the documentation start page' })).toHaveAttribute('href', '/docs');
    expect(docsApi.getPage).toHaveBeenCalledTimes(1);
  });
});
