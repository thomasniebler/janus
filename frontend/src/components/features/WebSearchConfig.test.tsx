import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { webSearchApi } from '../../api';
import { WebSearchConfig } from './WebSearchConfig';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const unconfigured = { provider: null, api_key_configured: false, base_url: null, configured: false };

const renderConfig = (isAdmin: boolean) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WebSearchConfig isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('WebSearchConfig', () => {
  it('lets an admin enable a self-hosted SearXNG backend', async () => {
    vi.mocked(webSearchApi.getSettings).mockResolvedValue(response(unconfigured));
    vi.mocked(webSearchApi.updateSettings).mockResolvedValue(
      response({ provider: 'searxng', api_key_configured: false, base_url: 'http://localhost:8888', configured: true })
    );

    renderConfig(true);
    const user = userEvent.setup();

    expect(await screen.findByText('Not configured')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/provider/i), 'searxng');
    await user.type(screen.getByLabelText(/searxng base url/i), 'http://localhost:8888');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(webSearchApi.updateSettings).toHaveBeenCalledWith({
      provider: 'searxng',
      base_url: 'http://localhost:8888',
    });
    expect(await screen.findByText(/web search settings saved/i)).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
  });

  it('is read-only for non-admin users', async () => {
    vi.mocked(webSearchApi.getSettings).mockResolvedValue(response(unconfigured));

    renderConfig(false);

    expect(await screen.findByText(/only an administrator can change/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/provider/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save settings/i })).not.toBeInTheDocument();
  });
});
