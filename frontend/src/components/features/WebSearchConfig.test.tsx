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

  it('offers the AI providers next to SearXNG and Brave, and saves one with its model', async () => {
    vi.mocked(webSearchApi.getSettings).mockResolvedValue(
      response({
        ...unconfigured,
        default_models: { openai: 'gpt-4.1-mini', gemini: 'gemini-2.5-flash' },
        provider_keys: { openai: true, gemini: false },
      })
    );
    vi.mocked(webSearchApi.updateSettings).mockResolvedValue(
      response({ ...unconfigured, provider: 'openai', model: 'gpt-x', configured: true, provider_keys: { openai: true } })
    );

    renderConfig(true);
    const user = userEvent.setup();

    await screen.findByText('Not configured');
    const options = Array.from((screen.getByLabelText('Provider') as HTMLSelectElement).options).map((option) => option.value);
    expect(options).toEqual(['', 'searxng', 'brave', 'openai', 'gemini', 'anthropic', 'openrouter']);

    await user.selectOptions(screen.getByLabelText('Provider'), 'openai');
    expect(screen.getByLabelText('Search model')).toHaveAttribute('placeholder', 'gpt-4.1-mini');
    expect(screen.getByTestId('web-search-cloud-hint')).toHaveTextContent('sent to OpenAI');
    expect(screen.queryByTestId('web-search-key-missing')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Search model'), ' gpt-x ');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(webSearchApi.updateSettings).toHaveBeenCalledWith({ provider: 'openai', model: 'gpt-x' });
    expect(await screen.findByText('Enabled')).toBeInTheDocument();
  });

  it('warns when the chosen provider has no stored chat key', async () => {
    vi.mocked(webSearchApi.getSettings).mockResolvedValue(response({ ...unconfigured, provider_keys: { gemini: false } }));

    renderConfig(true);
    const user = userEvent.setup();

    await screen.findByText('Not configured');
    await user.selectOptions(screen.getByLabelText('Provider'), 'gemini');

    expect(screen.getByTestId('web-search-key-missing')).toHaveTextContent('No Google Gemini API key is stored yet');
  });

  it('starts a native provider from its default model when switching provider', async () => {
    vi.mocked(webSearchApi.getSettings).mockResolvedValue(
      response({ ...unconfigured, provider: 'openai', model: 'gpt-x', provider_keys: { openai: true, gemini: true } })
    );

    renderConfig(true);
    const user = userEvent.setup();

    expect(await screen.findByLabelText('Search model')).toHaveValue('gpt-x');
    await user.selectOptions(screen.getByLabelText('Provider'), 'gemini');
    expect(screen.getByLabelText('Search model')).toHaveValue('');
  });
});
